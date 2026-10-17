import { lazy, Suspense } from "react";
import { buildTrendSeries, pointValue } from "../chart-contract";
import type { Observation } from "../types";
const Canvas = lazy(() =>
  import("./EChartCanvas").then((module) => ({ default: module.DataChart })),
);
export function DataChart(props: {
  rows: Observation[];
  kind?: "trend" | "comparison";
  onEvidence: (id: string) => void;
}) {
  const chartable =
    props.kind === "comparison"
      ? props.rows.length > 0 &&
        props.rows.every((row) => pointValue(row) !== null)
      : buildTrendSeries(props.rows).length > 0;
  if (!chartable)
    return (
      <p className="chart-empty">
        这组披露保留了区间、上下限或非数值信息，请在数据表查看完整内容。
      </p>
    );
  return (
    <Suspense
      fallback={
        <div role="status" className="chart-empty">
          正在准备图表…
        </div>
      }
    >
      <Canvas {...props} />
    </Suspense>
  );
}
