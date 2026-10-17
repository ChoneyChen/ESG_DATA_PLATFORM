import { useEffect, useRef } from "react";
import type { EChartsCoreOption } from "echarts/core";
import { getInstanceByDom, init, use as register } from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  AriaComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import {
  buildTrendSeries,
  chartDescription,
  pointValue,
} from "../chart-contract";
import { formatValue, unitLabel } from "../format";
import type { Observation } from "../types";
register([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  AriaComponent,
  CanvasRenderer,
]);

export function DataChart({
  rows,
  kind = "trend",
  onEvidence,
}: {
  rows: Observation[];
  kind?: "trend" | "comparison";
  onEvidence: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const series = kind === "trend" ? buildTrendSeries(rows)[0] : null;
  const chartRows = kind === "trend" ? series?.rows || [] : rows;
  const chartable =
    kind === "trend"
      ? !!series
      : rows.length > 0 && rows.every((row) => pointValue(row) !== null);
  const option: EChartsCoreOption = {
    animation: false,
    aria: { enabled: true, description: chartDescription(rows) },
    color: ["#14746c"],
    grid: {
      left: kind === "comparison" ? 130 : 70,
      right: 35,
      top: 25,
      bottom: 48,
      containLabel: true,
    },
    tooltip: {
      trigger: "item",
      confine: true,
      renderMode: "richText",
      formatter: (value: unknown) => {
        const index = (value as { dataIndex: number }).dataIndex;
        const row = chartRows[index];
        return row
          ? [
              kind === "comparison"
                ? row.entity_name
                : row.period_end?.slice(0, 4),
              formatValue(row) + " " + unitLabel(row.canonical_unit),
            ].join("\n")
          : "没有已发布数值";
      },
    },
    xAxis:
      kind === "trend"
        ? {
            type: "category",
            data: series?.years,
            boundaryGap: false,
            axisLine: { lineStyle: { color: "#b8c9c5" } },
            axisLabel: { color: "#526660" },
          }
        : {
            type: "value",
            axisLabel: { color: "#526660" },
            splitLine: { lineStyle: { color: "#edf1ef" } },
          },
    yAxis:
      kind === "trend"
        ? {
            type: "value",
            axisLabel: { color: "#526660" },
            splitLine: { lineStyle: { color: "#edf1ef" } },
          }
        : {
            type: "category",
            data: rows.map((row) => row.entity_name),
            inverse: true,
            axisTick: { show: false },
            axisLine: { show: false },
            axisLabel: { color: "#344f45", width: 120, overflow: "truncate" },
          },
    series: [
      kind === "trend"
        ? {
            type: "line",
            connectNulls: false,
            data: series?.points,
            symbolSize: 9,
            lineStyle: { width: 3 },
            itemStyle: { borderColor: "white", borderWidth: 2 },
            emphasis: { scale: 1.4 },
          }
        : {
            type: "bar",
            data: rows.map(pointValue),
            barMaxWidth: 28,
            itemStyle: { borderRadius: [0, 4, 4, 0] },
          },
    ],
  };
  const optionRef = useRef(option);
  optionRef.current = option;
  const evidenceRef = useRef(onEvidence);
  evidenceRef.current = onEvidence;
  const rowsRef = useRef(chartRows);
  rowsRef.current = chartRows;
  useEffect(() => {
    if (!chartable || !host.current || host.current.clientWidth === 0) return;
    const chart = init(host.current, undefined, { renderer: "canvas" });
    chart.setOption(optionRef.current);
    chart.on("click", (event) => {
      const row = rowsRef.current[event.dataIndex];
      if (row) evidenceRef.current(row.observation_id);
    });
    const resize = () => chart.resize();
    const observer =
      typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    observer?.observe(host.current);
    window.addEventListener("resize", resize);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [chartable, kind]);
  useEffect(() => {
    if (host.current && chartable)
      getInstanceByDom(host.current)?.setOption(optionRef.current, true);
  }, [rows, chartable]);
  if (!chartable)
    return (
      <p className="chart-empty">
        这组披露保留了区间、上下限或非数值信息，请在数据表查看完整内容。
      </p>
    );
  return (
    <div
      className="data-chart"
      ref={host}
      role="img"
      aria-label={chartDescription(rows)}
      style={{
        height:
          kind === "comparison" ? Math.max(240, rows.length * 48 + 80) : 285,
      }}
    />
  );
}
