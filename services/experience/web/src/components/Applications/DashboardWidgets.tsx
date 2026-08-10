import ReactECharts from "echarts-for-react";
import { Card } from "@blueprintjs/core";
import type { DashboardWidget } from "../../api/experience";
import { useThemeStore } from "../../store/theme";

export function DashboardWidgets({ widgets }: { widgets: DashboardWidget[] }) {
  if (widgets.length === 0) {
    return (
      <p className="hl-text-muted">
        No widgets configured yet — go to the Builder tab, enable Dashboard, and drag a KPI or Table widget onto
        the canvas (each one needs an ObjectType selected before saving, or it won't be included).
      </p>
    );
  }
  return (
    <div className="hl-dashboard-grid">
      {widgets.map((widget, i) => (
        <Card key={i}>
          <div className="hl-widget-label">{widget.label}</div>
          {widget.component === "kpi" && <KpiWidget value={widget.value ?? 0} />}
          {widget.component === "table" && <TableWidget rows={widget.rows ?? []} />}
          {widget.component !== "kpi" && widget.component !== "table" && <PluginWidget widget={widget} />}
        </Card>
      ))}
    </div>
  );
}

function KpiWidget({ value }: { value: number }) {
  const resolved = useThemeStore((s) => s.resolved);
  const textColor = resolved === "dark" ? "#e8eaed" : "#1c2127";

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
        detail: { valueAnimation: true, fontSize: 32, color: textColor, offsetCenter: [0, 0] },
        data: [{ value }],
      },
    ],
  };
  return <ReactECharts option={option} className="hl-chart-sm" />;
}

function TableWidget({ rows }: { rows: Array<Record<string, unknown>> }) {
  const keys = rows[0] ? Object.keys(rows[0]).slice(0, 4) : [];
  return (
    <table className="hl-data-table hl-data-table-compact">
      <thead>
        <tr>
          {keys.map((k) => (
            <th key={k}>{k}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 6).map((row, i) => (
          <tr key={i} className="hl-data-table-row">
            {keys.map((k) => (
              <td key={k} className="hl-mono">
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
    return <p className="hl-text-muted">Plugin component "{widget.component}" — no iframe URL declared.</p>;
  }
  return (
    <iframe
      src={widget.iframeUrl}
      title={widget.label}
      className="hl-plugin-iframe"
      sandbox=""
      referrerPolicy="no-referrer"
      loading="lazy"
    />
  );
}
