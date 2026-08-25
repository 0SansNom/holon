import EChartsReactCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart, GaugeChart, PieChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, GaugeChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer]);

const Chart =
  (EChartsReactCore as unknown as { default?: typeof EChartsReactCore }).default ?? EChartsReactCore;

export const ECharts = (props: React.ComponentProps<typeof EChartsReactCore>) => (
  <Chart {...props} echarts={echarts} />
);
