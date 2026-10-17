import { apiUrl } from "../api";
import type { Dataset } from "../types";
import { Icon } from "./Ui";
import { ProductLink } from "./ProductLink";
export default function Methods({
  dataset,
  datasets,
}: {
  dataset: string;
  datasets: Dataset[];
}) {
  return (
    <>
      <div className="page-heading">
        <div className="eyebrow">DATA & METHODS</div>
        <h1>数据与方法</h1>
        <p>理解数据从哪里来、如何表达，以及在什么条件下可以比较。</p>
      </div>
      <div className="methods-layout">
        <nav aria-label="方法章节">
          <a href="#data-source">数据来源</a>
          <a href="#data-definition">定义与口径</a>
          <a href="#data-comparison">比较与趋势</a>
          <a href="#data-missing">缺失与限制</a>
          <a href="#data-versions">版本与下载</a>
        </nav>
        <div className="method-articles">
          <section id="data-source">
            <span className="eyebrow">01 · 来源</span>
            <h2>以企业报告为来源</h2>
            <p>
              平台的数据来自企业公开披露。每条已发布数据保留报告标题、披露片段和可获得的页码，使读者能够核验数值及其上下文。演示主体和合成数据在网站中明确标注，不代表真实企业披露。
            </p>
            <ProductLink view="sources" className="text-button">
              查看报告来源 <Icon name="arrow" size={16} />
            </ProductLink>
          </section>
          <section id="data-definition">
            <span className="eyebrow">02 · 定义</span>
            <h2>同一指标，也要保留完整口径</h2>
            <p>
              我们以指标定义、主体、报告期间、披露边界、计量方法和必要维度共同识别一条数据。标准单位转换保留原始披露值与单位。ESRS
              原始条款编号始终显示，便于查阅和追踪。
            </p>
            <p>
              取水与耗水独立记录；范围 2
              的地点法与市场法独立记录；污染物按名称、介质及单位分别组织。企业报告中的子公司数据也不自动等同于集团数据。
            </p>
            <ProductLink view="metrics" className="text-button">
              浏览指标定义 <Icon name="arrow" size={16} />
            </ProductLink>
          </section>
          <section id="data-comparison">
            <span className="eyebrow">03 · 比较</span>
            <h2>比较的是同口径数据</h2>
            <p>
              同行比较先检查期间、边界、方法、定义版本与必要维度，返回符合条件的结果及未纳入原因。存在多个独立口径时，需要选择一个口径。未知信息不会自动认定为一致。
            </p>
            <p>
              趋势图只连接同一企业、同一口径的明确数值；缺失年份留空。排放、能耗或用水的绝对规模可以帮助理解企业经营，不能独立解释企业
              ESG 表现，也不构成评级或优劣排名。
            </p>
          </section>
          <section id="data-missing">
            <span className="eyebrow">04 · 缺失</span>
            <h2>未提供的数据不会被填成零</h2>
            <div className="missing-key">
              <div>
                <strong>0</strong>
                <span>明确披露零值</span>
              </div>
              <div>
                <strong>—</strong>
                <span>此版本暂无数据</span>
              </div>
              <div>
                <strong>&lt; / ≥</strong>
                <span>披露上下限</span>
              </div>
              <div>
                <strong>a – b</strong>
                <span>披露区间</span>
              </div>
            </div>
            <p>
              “已审核范围内未披露”“不适用”和“来源无法读取”只有在发布版本保留相应声明时才显示。缺少数据本身不足以推断企业没有披露。上下限、区间与定性披露保留原义，不用中点代替。
            </p>
          </section>
          <section id="data-versions">
            <span className="eyebrow">05 · 版本</span>
            <h2>固定版本，复现一次查询</h2>
            <p>
              网站解析最新发布后，将明确的数据版本保存在链接中。企业、指标、比较、原文和下载使用相同版本；后续发布不会悄悄改变旧链接的数据。公开修订说明可以在数据来源详情中查看。
            </p>
            <div className="download-options">
              <a
                className={
                  "button primary " + (!dataset ? "disabled-link" : "")
                }
                aria-disabled={!dataset}
                href={
                  dataset
                    ? apiUrl("/v1/exports", {
                        format: "csv",
                        dataset_version: dataset,
                      })
                    : undefined
                }
              >
                <Icon name="download" size={17} /> 下载当前版本 CSV
              </a>
              <a
                className={
                  "button secondary " + (!dataset ? "disabled-link" : "")
                }
                aria-disabled={!dataset}
                href={
                  dataset
                    ? apiUrl("/v1/exports", {
                        format: "json",
                        dataset_version: dataset,
                      })
                    : undefined
                }
              >
                下载 JSON
              </a>
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>发布版本</th>
                    <th>发布时间</th>
                    <th>数据条数</th>
                  </tr>
                </thead>
                <tbody>
                  {datasets.map((item) => (
                    <tr key={item.dataset_version}>
                      <td>
                        {item.dataset_version}
                        {String(item.dataset_version) === dataset && (
                          <span className="badge green">当前浏览</span>
                        )}
                      </td>
                      <td>{item.created_at.replace("T", " ").slice(0, 19)}</td>
                      <td>{item.observation_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!datasets.length && <p>尚无已发布数据版本。</p>}
          </section>
        </div>
      </div>
    </>
  );
}
