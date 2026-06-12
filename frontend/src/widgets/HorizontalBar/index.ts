import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

interface BarConfig {
  dp_id: string
}

WidgetRegistry.register({
  type: 'HorizontalBar',
  label: 'widgets.horizontalbar.title',
  icon: '📊',
  group: 'Anzeige',
  minW: 3, minH: 2,
  defaultW: 6, defaultH: 3,
  component: Widget,
  configComponent: Config,
  noDatapoint: true,
  defaultConfig: {
    label: '',
    bars: [],
    colors: ['#22c55e', '#f59e0b', '#ef4444'],
    show_value: true,
  },
  compatibleTypes: ['*'],
  getExtraDatapointIds: (config) => {
    const bars = config.bars as BarConfig[] | undefined
    return (bars ?? []).map(b => b.dp_id).filter(Boolean)
  },
})
