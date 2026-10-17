import type { ReportedProperty } from "../types";

function propertyLabel(property: ReportedProperty): string {
  return typeof property.label_zh === "string" && property.label_zh.trim()
    ? property.label_zh
    : "补充信息";
}

function PropertyValue({ property }: { property: ReportedProperty }) {
  const value = property.value_raw;
  if (typeof value === "string")
    return (
      <span className="reported-property-value">{value || "原字段为空"}</span>
    );
  if (Array.isArray(value) && value.every((item) => typeof item === "string"))
    return value.length ? (
      <ul className="reported-property-list">
        {value.map((item, index) => (
          <li key={index}>{item || "原字段为空"}</li>
        ))}
      </ul>
    ) : (
      <span className="muted">原报告记录为空列表</span>
    );
  return <span className="muted">此项内容尚未结构化，请查看报告原文。</span>;
}

export function ReportedPropertySummary({
  properties,
}: {
  properties?: ReportedProperty[];
}) {
  if (!Array.isArray(properties) || !properties.length) return null;
  const labels = properties.slice(0, 2).map(propertyLabel).join("、");
  return (
    <span className="table-subtitle reported-properties-preview">
      报告补充信息 · {properties.length} 项：{labels}
      {properties.length > 2 ? "等" : ""}
    </span>
  );
}

export function ReportedProperties({
  properties,
}: {
  properties?: ReportedProperty[];
}) {
  if (!Array.isArray(properties) || !properties.length) return null;
  return (
    <section className="drawer-section reported-properties">
      <h3>报告补充信息</h3>
      <details className="reported-properties-disclosure">
        <summary>查看 {properties.length} 项报告补充信息</summary>
        <p className="muted reported-properties-note">
          保留报告字段的原值、原单位与引用，不改变此指标的计量口径。
        </p>
        <div className="reported-property-cards">
          {properties.map((property, index) => {
            const fieldEvidence = property.evidence_scope === "field";
            const evidence = Array.isArray(property.evidence)
              ? property.evidence
              : [];
            return (
              <article
                className="reported-property-card"
                key={`${property.field_key}:${index}`}
              >
                <dl className="reported-property-field">
                  <dt>{propertyLabel(property)}</dt>
                  <dd>
                    <PropertyValue property={property} />
                    {typeof property.unit_raw === "string" &&
                      property.unit_raw && (
                        <span className="reported-property-unit">
                          原单位：{property.unit_raw}
                        </span>
                      )}
                  </dd>
                </dl>
                {property.source_role === "operand" && (
                  <p className="muted reported-property-report">
                    计算来源：
                    {typeof property.source_label === "string" &&
                    property.source_label.trim()
                      ? property.source_label
                      : "计算操作数"}
                  </p>
                )}
                {typeof property.report_title === "string" &&
                  property.report_title && (
                    <p className="muted reported-property-report">
                      来源报告：{property.report_title}
                    </p>
                  )}
                <div className="reported-property-evidence">
                  <strong>{fieldEvidence ? "字段证据" : "来源级说明"}</strong>
                  {!fieldEvidence && (
                    <p className="muted">
                      以下为报告来源的说明，未提供此字段的单独引用。
                    </p>
                  )}
                  {evidence.length ? (
                    evidence.map((source, sourceIndex) => (
                      <figure className="source-quote" key={sourceIndex}>
                        <figcaption>
                          {fieldEvidence ? "字段引用" : "来源说明"}
                          {typeof source.page_number === "number" &&
                          Number.isFinite(source.page_number)
                            ? ` · 文档页码 ${source.page_number}`
                            : " · 未提供页码"}
                        </figcaption>
                        <blockquote>
                          {typeof source.source_quote === "string" &&
                          source.source_quote
                            ? source.source_quote
                            : "未提供引用片段。"}
                        </blockquote>
                      </figure>
                    ))
                  ) : (
                    <p className="muted">
                      未提供引用片段或页码，请查看报告原文。
                    </p>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </details>
    </section>
  );
}
