import ReactECharts from "echarts-for-react";
import { Card } from "@blueprintjs/core";
import type { DashboardWidget } from "../../api/experience";

export function DashboardWidgets({ widgets }: { widgets: DashboardWidget[] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 16 }}>
      {widgets.map((widget, i) => (
        <Card key={i}>
          <div style={{ fontSize: 12, color: "var(--hl-text-muted)", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.03em" }}>
            {widget.label}
          </div>
          {widget.component === "kpi" && <KpiWidget value={widget.value ?? 0} />}
          {widget.component === "table" && <TableWidget rows={widget.rows ?? []} />}
          {widget.component !== "kpi" && widget.component !== "table" && <PluginWidget widget={widget} />}
        </Card>
      ))}
    </div>
  );
}

function KpiWidget({ value }: { value: number }) {
  const option = {
    series: [
      {
        type: "gauge",
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: Math.max(value * 1.5, 10),
        progress: { show: true, width: 10 },
        axisLine: { lineStyle: { width: 10 } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        pointer: { show: false },
        detail: { valueAnimation: true, fontSize: 32, color: "#d7dae0", offsetCenter: [0, 0] },
        data: [{ value }],
      },
    ],
  };
  return <ReactECharts option={option} style={{ height: 160 }} theme="dark" />;
}

function TableWidget({ rows }: { rows: Array<Record<string, unknown>> }) {
  const keys = rows[0] ? Object.keys(rows[0]).slice(0, 4) : [];
  return (
    <table style={{ width: "100%", fontSize: 12 }}>
      <thead>
        <tr>
          {keys.map((k) => (
            <th key={k} style={{ textAlign: "left", color: "var(--hl-text-muted)", padding: "4px 6px" }}>
              {k}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 6).map((row, i) => (
          <tr key={i} style={{ borderTop: "1px solid var(--hl-border)" }}>
            {keys.map((k) => (
              <td key={k} className="hl-mono" style={{ padding: "4px 6px" }}>
                {String(row[k] ?? "—")}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PluginWidget({ widget }: { widget: DashboardWidget }) {
  if (!widget.iframeUrl) {
    return <p style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>Plugin component "{widget.component}" — no iframe URL declared.</p>;
  }
  return (
    <iframe
      src={widget.iframeUrl}
      title={widget.label}
      style={{ width: "100%", height: 160, border: "1px solid var(--hl-border)", borderRadius: 4, background: "#fff" }}
      sandbox=""
    />
  );
}
