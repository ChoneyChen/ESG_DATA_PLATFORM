import type { Evidence } from "../types";
import { contextLabel, periodLabel } from "../format";
import { Icon } from "./Ui";

export function ReviewStamp({
  revisions = 0,
  label = "已复核",
}: {
  revisions?: number;
  label?: string;
}) {
  return (
    <span className="stamp" title="通过发布前复核，来源、口径与版本均可追溯">
      <Icon name="shield" size={13} />
      {label}
      {revisions > 0 && <span className="stamp-count">v{revisions + 1}</span>}
    </span>
  );
}

function stamp(value?: string | null) {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 16);
}

/**
 * The reviewer's logic, shown as the consumer sees it: every published value
 * carries a readable source, a complete measurement context, a comparison
 * verdict and a frozen dataset version. Internal identities stay in the
 * console; this is the public proof trail.
 */
export function ReviewTrail({ item }: { item: Evidence }) {
  const quote = item.sources?.find((source) => source.source_quote);
  const checks = [
    {
      key: "source",
      title: "原文摘录",
      detail: item.sources?.length
        ? `${item.sources.length} 处披露片段${
            quote?.page_number ? ` · 第 ${quote.page_number} 页` : ""
          }`
        : "未附原文片段",
      ok: Boolean(item.sources?.length),
    },
    {
      key: "context",
      title: "口径核对",
      detail: [
        contextLabel(item.boundary_code),
        contextLabel(item.measurement_method),
        periodLabel(item),
      ]
        .filter(Boolean)
        .join(" · "),
      ok: Boolean(
        item.boundary_code &&
        item.measurement_method &&
        item.period_start &&
        item.period_end,
      ),
    },
    {
      key: "verdict",
      title: "比较判定",
      detail:
        {
          comparable: "同口径，可比",
          eligible: "同口径，可比",
          conditional: "有条件比较",
          display_only: "仅展示原值",
        }[item.comparison_eligibility] || "仅展示原值",
      ok: item.comparison_eligibility !== "unknown",
    },
    {
      key: "release",
      title: "固定版本发布",
      detail: `数据版本 ${item.dataset_version}${
        item.published_at ? " · " + stamp(item.published_at) : ""
      }`,
      ok: Boolean(item.dataset_version),
    },
  ];
  const revisions = item.public_revisions || [];
  return (
    <section className="review-trail-card">
      <div className="review-trail-head">
        <span className="review-trail-title">
          <Icon name="shield" size={15} />
          复核凭证
        </span>
        <span className="muted">发布前逐项核对</span>
      </div>
      <ol className="review-trail">
        {checks.map((check, index) => (
          <li key={check.key} className={check.ok ? "done" : "pending"}>
            <span className="trail-mark">
              {check.ok ? <Icon name="check" size={12} /> : index + 1}
            </span>
            <div>
              <strong>{check.title}</strong>
              <small>{check.detail}</small>
            </div>
          </li>
        ))}
      </ol>
      {revisions.length > 0 && (
        <p className="review-trail-note">
          自首次发布以来发生 {revisions.length} 次公开修订，下方保留全部说明。
        </p>
      )}
    </section>
  );
}
