/**
 * Automatic review of evidence-proven candidates.
 *
 * This module adds no new review semantics: it reuses the same proposal and
 * machine verification rules a person would see, then applies them only where
 * every publication requirement is already backed by captured evidence. A
 * candidate that needs a judgement (partial mapping, narrower boundary,
 * missing axis, unsupported unit) is left untouched and reported by cause.
 *
 * It is report-scoped, idempotent and records an explicit operator identity so
 * every automatic decision remains auditable and reversible.
 */
import {candidateProposals, machineVerify, VERIFICATION_RULE_VERSION} from './candidate-review.mjs';
import {collapseDuplicateCandidates} from './report-artifacts.mjs';

export const AUTO_REVIEW_VERSION = 'auto-review-v1';
export const AUTO_REVIEW_OPERATOR = 'auto-review:evidence-v1';
const DEFAULT_REASON = `自动审核 ${AUTO_REVIEW_VERSION}：仅当全部发布条件已由交付证据证明时批准；其余保留人工审核。`;

const blockerKey = item => (item.blockers || []).includes('mapping_exact') && item.mapping_relation !== 'exact'
  ? `mapping_${item.mapping_relation}` : null;

export async function autoReview(db, {report_ref = null, limit = 1000, apply = false,
                                       operator_ref = AUTO_REVIEW_OPERATOR, reason = null} = {}) {
  let proposals = await candidateProposals(db, {report_ref, limit});
  const reports = report_ref ? [report_ref] : [...new Set((proposals.items || []).map(item => item.report_ref))];
  let duplicatesFound = 0, duplicatesCollapsed = 0;
  for (const ref of reports) {
    const collapsed = await collapseDuplicateCandidates(db, {report_ref: ref, apply,
      operator_ref, reason: reason?.trim() || DEFAULT_REASON});
    duplicatesFound += collapsed.duplicate_candidates || 0;
    duplicatesCollapsed += collapsed.collapsed || 0;
  }
  if (apply && duplicatesCollapsed) proposals = await candidateProposals(db, {report_ref, limit});
  const items = proposals.items || [];
  const pending = items.filter(item => !item.already_approved);
  const ready = pending.filter(item => item.resolvable);
  const blocked = pending.filter(item => !item.resolvable);
  const blockers = {}, relations = {};
  for (const item of blocked) {
    for (const code of item.blockers || []) blockers[code] = (blockers[code] || 0) + 1;
    const key = blockerKey(item);
    if (key) relations[key] = (relations[key] || 0) + 1;
  }
  const summary = {
    version: AUTO_REVIEW_VERSION, verification_rule_version: VERIFICATION_RULE_VERSION,
    report_ref, considered: items.length, already_approved: items.length - pending.length,
    duplicates_found: duplicatesFound, duplicates_collapsed: duplicatesCollapsed,
    ready: ready.length, needs_human: blocked.length,
    applied: 0, blockers, relations,
  };
  if (!apply || !ready.length) return {...summary, apply: false};

  const result = await machineVerify(db, {
    mapping_ids: ready.map(item => item.mapping_id), apply: true,
    operator_ref: operator_ref.trim(), reason: reason?.trim() || DEFAULT_REASON,
  });
  return {...summary, apply: true, applied: result.applied,
    needs_human: blocked.length + (ready.length - result.applied)};
}
