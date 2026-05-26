import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'WidgetRef',
  label: 'Widget-Referenz',
  icon: '🔗',
  group: 'Medien & Sonstiges',
  minW: 2, minH: 2,
  defaultW: 3, defaultH: 2,
  component: Widget,
  configComponent: Config,
  defaultConfig: { source_page_id: null, source_widget_name: null },
  compatibleTypes: ['*'],
  noDatapoint: true,
  supportsStatusDatapoint: false,
  getExtraDatapointIds: () => [],
})
