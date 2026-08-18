import { useMemo, useState } from "react";
import { ECharts } from "../common/ECharts";
import { Button, HTMLSelect, Tag } from "@blueprintjs/core";
import { useThemeStore } from "../../store/theme";
import {
  buildExplorationSeries,
  type ExplorationBucket,
  type ExplorationChartKind,
} from "./explorationCharts";

type PreferredKind = ExplorationChartKind | "auto";

export function ExplorationChartPanel({
  rows,
  propertyKeys,
  propertyMapping,
  onDrillDown,
  collapsed,
  onToggleCollapsed,
}: {
  rows: Record<string, unknown>[];
  propertyKeys: string[];
  propertyMapping?: Record<string, string> | null;
  onDrillDown: (bucket: ExplorationBucket, property: string) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}) {
  const resolvedTheme = useThemeStore((s) => s.resolved);
  const [property, setProperty] = useState(propertyKeys[0] ?? "");
  const [preferredKind, setPreferredKind] = useState<PreferredKind>("auto");

  const effectiveProperty = propertyKeys.includes(property) ? property : (propertyKeys[0] ?? "");

  const series = useMemo(
    () =>
      buildExplorationSeries(rows, effectiveProperty, preferredKind, propertyMapping ?? {}),
    [rows, effectiveProperty, preferredKind, propertyMapping],
  );

  const textColor = resolvedTheme === "dark" ? "#e8eaed" : "#1c2127";
  const mutedColor = resolvedTheme === "dark" ? "#9aa0a6" : "#5f6b7c";
  const accent = resolvedTheme === "dark" ? "#8ab4f8" : "#2d72d2";

  const option = useMemo(() => {
    if (!series || series.buckets.length === 0) return null;
    const labels = series.buckets.map((b) => b.label);
    const counts = series.buckets.map((b) => b.count);
    if (series.kind === "pie") {
      return {
        tooltip: { trigger: "item" },
        series: [
          {
            type: "pie",
            radius: ["35%", "70%"],
            data: series.buckets.map((b) => ({ name: b.label, value: b.count, bucket: b })),
            label: { color: textColor, fontSize: 11 },
          },
        ],
      };
    }
    return {
      tooltip: { trigger: "axis" },
      grid: { left: 40, right: 16, top: 24, bottom: series.kind === "histogram" ? 48 : 32 },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { color: mutedColor, rotate: labels.some((l) => l.length > 8) ? 30 : 0, fontSize: 11 },
        axisLine: { lineStyle: { color: mutedColor } },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        axisLabel: { color: mutedColor, fontSize: 11 },
        splitLine: { lineStyle: { color: resolvedTheme === "dark" ? "#3c4043" : "#e6e8eb" } },
      },
      series: [
        {
          type: "bar",
          data: counts.map((count, i) => ({ value: count, bucket: series.buckets[i] })),
          itemStyle: { color: accent },
          barMaxWidth: 48,
        },
      ],
    };
  }, [series, textColor, mutedColor, accent, resolvedTheme]);

  function handleChartClick(params: { data?: { bucket?: ExplorationBucket } }) {
    const bucket = params.data?.bucket;
    if (!bucket || !effectiveProperty) return;
    if (bucket.label.startsWith("Other (") && !Array.isArray(bucket.value)) return;
    onDrillDown(bucket, effectiveProperty);
  }

  return (
    <div className="hl-oe-explore-chart hl-mb-md">
      <div className="hl-flex-between hl-items-center">
        <div className="hl-flex-row hl-items-center hl-gap-sm">
          <div className="hl-section-title" style={{ margin: 0 }}>
            Exploration
          </div>
          {series && (
            <Tag minimal>
              {series.kind} · {series.total} rows
            </Tag>
          )}
        </div>
        <Button minimal small icon={collapsed ? "chevron-down" : "chevron-up"} onClick={onToggleCollapsed}>
          {collapsed ? "Show chart" : "Hide"}
        </Button>
      </div>

      {!collapsed && (
        <>
          <div className="hl-flex-row hl-gap-sm hl-items-center hl-mt-sm hl-mb-sm" style={{ flexWrap: "wrap" }}>
            <HTMLSelect
              value={effectiveProperty}
              disabled={propertyKeys.length === 0}
              onChange={(e) => setProperty(e.target.value)}
            >
              {propertyKeys.length === 0 && <option value="">No properties</option>}
              {propertyKeys.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </HTMLSelect>
            <HTMLSelect value={preferredKind} onChange={(e) => setPreferredKind(e.target.value as PreferredKind)}>
              <option value="auto">Auto</option>
              <option value="bar">Bar</option>
              <option value="pie">Pie</option>
              <option value="histogram">Histogram</option>
            </HTMLSelect>
            <span className="hl-text-muted-sm">Click a slice/bar to filter the table</span>
          </div>

          {propertyKeys.length === 0 || !series || !option ? (
            <p className="hl-text-muted-sm">No data to chart for this property.</p>
          ) : (
            <ECharts
              option={option}
              className="hl-oe-chart"
              onEvents={{ click: handleChartClick }}
              notMerge
            />
          )}
        </>
      )}
    </div>
  );
}
