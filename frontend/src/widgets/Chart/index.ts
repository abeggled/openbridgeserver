import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Chart',
  label: 'widgets.chart.title',
  icon: '📈',
  group: 'Anzeige',
  minW: 4, minH: 3,
  defaultW: 6, defaultH: 4,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    time_range: 'last_7d',
    chart_type: 'line',
    primary_color: '#3b82f6',
    primary_axis: 'left',
    series: [],
  },
  compatibleTypes: ['FLOAT', 'INTEGER'],
  getExtraDatapointIds: (config) => {
    const series = config.series as Array<{ dp_id?: string }> | undefined
    if (!Array.isArray(series)) return []
    return series.map(s => s.dp_id).filter((id): id is string => typeof id === 'string' && id.length > 0)
  },
})
