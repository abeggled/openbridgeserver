import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Link',
  label: 'Link',
  icon: '🔗',
  group: 'Medien & Sonstiges',
  minW: 2, minH: 2,
  defaultW: 2, defaultH: 2,
  component: Widget,
  configComponent: Config,
  defaultConfig: { label: '', icon: '🔗', target_node_id: '' },
  compatibleTypes: ['*'],
  noDatapoint: true,
  getExtraDatapointIds: () => [],
})
