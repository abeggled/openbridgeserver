import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Chart',
  label: 'Verlauf',
  icon: '📈',
  group: 'Anzeige',
  minW: 4, minH: 3,
  defaultW: 6, defaultH: 4,
  component: Widget,
  configComponent: Config,
  defaultConfig: { label: '', hours: 24 },
  compatibleTypes: ['FLOAT', 'INTEGER'],
})
