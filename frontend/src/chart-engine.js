import { init as create, use } from 'echarts/core';
import { LineChart, BarChart, HeatmapChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, VisualMapComponent, MarkLineComponent, MarkPointComponent, TitleComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

use([LineChart, BarChart, HeatmapChart, GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, VisualMapComponent, MarkLineComponent, MarkPointComponent, TitleComponent, CanvasRenderer]);
export function init(node) {
  const fontFamily = getComputedStyle(node).fontFamily;
  return create(node, {
    animation: false,
    textStyle: { fontFamily, color: '#505c6b', fontSize: 13 },
    tooltip: { confine: true, textStyle: { fontFamily, fontSize: 14 } },
    categoryAxis: { axisLine: { lineStyle: { color: '#e5e9ef' } } },
    valueAxis: { splitLine: { lineStyle: { color: '#eef0f3', type: 'dashed' } } },
  });
}
