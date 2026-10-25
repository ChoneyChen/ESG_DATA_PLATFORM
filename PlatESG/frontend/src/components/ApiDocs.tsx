import { useState } from "react";
import { Icon } from "./Ui";

interface Parameter {
  name: string;
  type: string;
  description: string;
  required?: boolean;
}
interface Endpoint {
  method: "GET" | "POST";
  path: string;
  summary: string;
  params?: Parameter[];
  body?: string;
  sample: string;
  response?: string;
}
const groups: {
  id: string;
  title: string;
  note: string;
  endpoints: Endpoint[];
}[] = [
  {
    id: "read",
    title: "数据读取",
    note: "发布版本是每个请求的基准；省略时返回最新公开版本。",
    endpoints: [
      {
        method: "GET",
        path: "/v1/datasets",
        summary: "列出公开发布版本及数据条数。",
        sample: "curl http://127.0.0.1:8765/api/v1/datasets",
        response: `{"latest": 12, "items": [{"dataset_version": 12, "created_at": "2026-10-24", "observation_count": 486}]}`,
      },
      {
        method: "GET",
        path: "/v1/entities",
        summary: "读取已发布企业及其覆盖指标数。",
        params: [
          {
            name: "dataset_version",
            type: "integer",
            description: "固定发布版本",
          },
          { name: "search", type: "string", description: "按企业名称检索" },
        ],
        sample:
          "curl 'http://127.0.0.1:8765/api/v1/entities?dataset_version=12&search=能源'",
      },
      {
        method: "GET",
        path: "/v1/metrics",
        summary: "读取指标定义、必要维度与比较条件。",
        params: [
          {
            name: "dataset_version",
            type: "integer",
            description: "固定发布版本",
          },
          {
            name: "topic",
            type: "string",
            description: "E1 / E2 / E3 / S1 / S2 / G1",
          },
        ],
        sample:
          "curl 'http://127.0.0.1:8765/api/v1/metrics?dataset_version=12&topic=E1'",
      },
      {
        method: "GET",
        path: "/v1/observations",
        summary: "按企业、指标与期间读取已发布数据，游标分页。",
        params: [
          {
            name: "dataset_version",
            type: "integer",
            description: "固定发布版本",
          },
          { name: "entity_ref", type: "string", description: "企业标识" },
          { name: "metric_id", type: "string", description: "指标标识" },
          {
            name: "period_start / period_end",
            type: "date",
            description: "期间过滤",
          },
          { name: "limit", type: "integer", description: "1–1000，默认 200" },
          { name: "cursor", type: "string", description: "上一页返回的游标" },
        ],
        sample:
          "curl 'http://127.0.0.1:8765/api/v1/observations?dataset_version=12&metric_id=metric-E1-6_07&limit=200'",
        response: `{"dataset_version": 12, "items": [{"observation_id": "…", "value_numeric": "128400.5", "canonical_unit": "tCO2e", …}], "next_cursor": null}`,
      },
      {
        method: "GET",
        path: "/v1/observations/{observation_id}",
        summary: "读取单条数据的原文片段、口径与公开修订。",
        params: [
          {
            name: "dataset_version",
            type: "integer",
            description: "固定发布版本",
          },
        ],
        sample:
          "curl 'http://127.0.0.1:8765/api/v1/observations/9f2c…?dataset_version=12'",
      },
      {
        method: "GET",
        path: "/v1/coverage",
        summary: "读取企业在各指标与期间上的发布覆盖。",
        params: [
          {
            name: "dataset_version",
            type: "integer",
            description: "固定发布版本",
          },
          { name: "entity_ref", type: "string", description: "企业标识" },
        ],
        sample:
          "curl 'http://127.0.0.1:8765/api/v1/coverage?dataset_version=12&entity_ref=…'",
      },
    ],
  },
  {
    id: "compare",
    title: "口径比较",
    note: "比较接口先核验期间、边界、方法与维度，返回纳入与未纳入原因。",
    endpoints: [
      {
        method: "POST",
        path: "/v1/comparisons",
        summary: "在同口径条件下比较多家企业。",
        body: `{
  "metric_id": "metric-E1-6_07",
  "entity_refs": ["entity-a", "entity-b"],
  "dataset_version": 12,
  "period_start": "2024-01-01",
  "period_end": "2024-12-31",
  "boundary_code": "consolidated_group",
  "dimensions": {"scope": "scope_1"}
}`,
        sample: `curl -X POST http://127.0.0.1:8765/api/v1/comparisons \\
  -H 'Content-Type: application/json' \\
  -d '{"metric_id":"metric-E1-6_07","entity_refs":["entity-a"],"dataset_version":12}'`,
      },
    ],
  },
  {
    id: "sources",
    title: "来源与原文",
    note: "来源目录只包含当前发布版本实际引用的报告。",
    endpoints: [
      {
        method: "GET",
        path: "/v1/sources",
        summary: "列出报告来源、发布者与数据引用数。",
        params: [
          {
            name: "dataset_version",
            type: "integer",
            description: "固定发布版本",
          },
        ],
        sample:
          "curl 'http://127.0.0.1:8765/api/v1/sources?dataset_version=12'",
      },
      {
        method: "GET",
        path: "/v1/sources/{source_id}/document",
        summary: "内联打开已核对哈希的原始报告 PDF。",
        params: [
          { name: "source_id", type: "sha256", description: "来源内容标识" },
          {
            name: "dataset_version",
            type: "integer",
            description: "固定发布版本",
          },
        ],
        sample:
          "curl 'http://127.0.0.1:8765/api/v1/sources/<source_id>/document?dataset_version=12'",
      },
    ],
  },
  {
    id: "export",
    title: "导出",
    note: "单次导出最多 100,000 条；CSV 与 JSON 使用同一公开字段集。",
    endpoints: [
      {
        method: "GET",
        path: "/v1/exports",
        summary: "按指标或企业导出当前版本的完整数据。",
        params: [
          {
            name: "format",
            type: "csv | json",
            description: "导出格式，默认 csv",
          },
          {
            name: "dataset_version",
            type: "integer",
            description: "固定发布版本",
          },
          {
            name: "entity_ref",
            type: "string",
            description: "限定企业（可选）",
          },
          {
            name: "metric_id",
            type: "string",
            description: "限定指标（可选）",
          },
        ],
        sample:
          "curl -OJ 'http://127.0.0.1:8765/api/v1/exports?format=csv&dataset_version=12'",
      },
    ],
  },
];

const conventions = [
  {
    title: "版本不可变",
    text: "同一 dataset_version 的数值、口径与来源保持不变；修订以新版本或公开修订说明出现。",
  },
  {
    title: "十进制原值",
    text: "数值以字符串返回，避免浮点误差；比率换算规则随数据一并提供。",
  },
  {
    title: "统一口径",
    text: "比较只纳入期间、边界、方法与必要维度一致的数据，其余返回原因。",
  },
  {
    title: "公开只读",
    text: "本页接口无需密钥，不暴露审核人、候选或内部任务信息。",
  },
];

function CodeBlock({ code, label }: { code: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="api-code">
      <div className="api-code-head">
        <span>{label || "请求"}</span>
        <button
          type="button"
          className="api-copy"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(code);
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1800);
            } catch {
              setCopied(false);
            }
          }}
        >
          <Icon name={copied ? "check" : "copy"} size={14} />
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}

export default function ApiDocs({ dataset }: { dataset: string }) {
  return (
    <div className="page api-docs">
      <header className="page-heading">
        <span className="eyebrow">API REFERENCE</span>
        <h1>API 文档</h1>
        <p>公开只读接口，与本页展示使用同一当前发布。</p>
      </header>
      <div className="api-meta">
        <div>
          <span>Base URL</span>
          <strong className="mono">/api</strong>
        </div>
        <div>
          <span>当前版本</span>
          <strong className="mono">
            {dataset ? "v" + dataset : "尚无发布"}
          </strong>
        </div>
        <div>
          <span>响应格式</span>
          <strong>JSON · CSV</strong>
        </div>
        <div>
          <span>鉴权</span>
          <strong>公开只读</strong>
        </div>
      </div>
      <div className="api-layout">
        <nav className="api-nav" aria-label="API 章节">
          {groups.map((group) => (
            <a key={group.id} href={"#" + group.id}>
              {group.title}
              <span>{group.endpoints.length}</span>
            </a>
          ))}
          <a href="#conventions">
            约定
            <span>{conventions.length}</span>
          </a>
          <div className="api-nav-foot">
            <Icon name="terminal" size={15} />
            <span>示例以本机 8765 端口为例</span>
          </div>
        </nav>
        <div className="api-content">
          {groups.map((group) => (
            <section className="api-group" id={group.id} key={group.id}>
              <div className="api-group-head">
                <h2>{group.title}</h2>
                <p>{group.note}</p>
              </div>
              {group.endpoints.map((endpoint) => (
                <article
                  className="endpoint"
                  key={endpoint.method + endpoint.path}
                >
                  <div className="endpoint-head">
                    <span className={"method " + endpoint.method.toLowerCase()}>
                      {endpoint.method}
                    </span>
                    <code className="endpoint-path">{endpoint.path}</code>
                    <span className="endpoint-summary">{endpoint.summary}</span>
                  </div>
                  {endpoint.params && (
                    <div className="param-table">
                      {endpoint.params.map((param) => (
                        <div className="param-row" key={param.name}>
                          <code>{param.name}</code>
                          <span className="param-type">{param.type}</span>
                          <span>{param.description}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {endpoint.body && (
                    <CodeBlock code={endpoint.body} label="请求体" />
                  )}
                  <CodeBlock code={endpoint.sample} />
                  {endpoint.response && (
                    <CodeBlock code={endpoint.response} label="响应示例" />
                  )}
                </article>
              ))}
            </section>
          ))}
          <section className="api-group" id="conventions">
            <div className="api-group-head">
              <h2>约定</h2>
              <p>接口与页面遵循同一公开合同。</p>
            </div>
            <div className="convention-grid">
              {conventions.map((item, index) => (
                <article key={item.title}>
                  <span className="mono">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <strong>{item.title}</strong>
                  <p>{item.text}</p>
                </article>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
