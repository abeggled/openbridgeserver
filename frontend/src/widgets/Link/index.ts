import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Link',
  label: 'widgets.link.title',
  icon: '🔗',
  group: 'Medien & Sonstiges',
  minW: 2, minH: 2,
  defaultW: 2, defaultH: 2,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    icon: '🔗',
    show_arrow: true,
    target_node_id: '',
    history_back: false,
    show_icon: true,
    preserve_icon_color: false,
    label_size: 'sm',
    active_indicator: 'none',
    icon_size: 0,
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
  getExtraDatapointIds: () => [],
})
