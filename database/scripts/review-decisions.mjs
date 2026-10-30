/** Decisions for existing SQL facts/mappings; independent of any bundle parser. */
import {randomUUID} from 'node:crypto';
export async function reviewRecord(db, { disclosure_id = null, mapping_id = null, decision, operator_ref, reason, mapping_relation = null, satisfaction_status = null }) {
  if ((!disclosure_id) === (!mapping_id) || !['approved', 'rejected', 'review_required'].includes(decision)
      || !operator_ref?.trim() || !reason?.trim()) throw new Error('Review requires one fact or mapping, a decision, an operator and a reason');
  return db.transaction(async tx => {
    if (disclosure_id && (mapping_relation != null || satisfaction_status != null)) throw new Error('Mapping semantics belong to a mapping review');
    if (mapping_relation != null && !['exact','broader','narrower','partial','conditional','alternative_measure','not_equivalent'].includes(mapping_relation))
      throw new Error('Invalid mapping relation');
    if (satisfaction_status != null && !['supports','partially_supports','not_sufficient','not_applicable','unknown'].includes(satisfaction_status))
      throw new Error('Invalid satisfaction status');
    if (mapping_id && (mapping_relation != null || satisfaction_status != null))
      await tx.query(`UPDATE esg.disclosure_standard_mappings SET mapping_relation=COALESCE($1,mapping_relation),satisfaction_status=COALESCE($2,satisfaction_status)
        WHERE disclosure_standard_mapping_id=$3`, [mapping_relation,satisfaction_status,mapping_id]);
    const table = disclosure_id ? 'esg.esg_disclosures' : 'esg.disclosure_standard_mappings';
    const key = disclosure_id ? 'disclosure_id' : 'disclosure_standard_mapping_id';
    const column = disclosure_id ? 'quality_status' : 'review_status';
    const result = await tx.query(`UPDATE ${table} SET ${column}=$1 WHERE ${key}=$2 RETURNING ${key}`, [decision, disclosure_id || mapping_id]);
    if (!result.rows.length) throw new Error('Review target does not exist');
    if (disclosure_id) await tx.query('UPDATE esg.esg_disclosures SET decided_at=CURRENT_TIMESTAMP WHERE disclosure_id=$1', [disclosure_id]);
    const decision_id = randomUUID();
    await tx.query('INSERT INTO esg.review_decisions(decision_id,disclosure_id,mapping_id,decision,operator_ref,reason,detail) VALUES($1,$2,$3,$4,$5,$6,$7)',
      [decision_id, disclosure_id, mapping_id, decision, operator_ref.trim(), reason.trim(), {mapping_relation,satisfaction_status}]);
    await tx.query(`INSERT INTO esg.outbox_events(event_id,event_type,target_ref) VALUES($1,'review',$2)`, [decision_id,disclosure_id || mapping_id]);
    return { decision_id, disclosure_id, mapping_id, decision, publication_status: 'not_published' };
  });
}
