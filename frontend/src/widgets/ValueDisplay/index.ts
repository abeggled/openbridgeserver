import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'ValueDisplay',
  label: 'Wertanzeige',
  icon: '🔢',
  minW: 2, minH: 2,
  defaultW: 3, defaultH: 2,
  component: Widget,
  configComponent: Config,
  defaultConfig: { label: '', decimals: 1 },
  compatibleTypes: ['FLOAT', 'INTEGER', 'BOOLEAN', 'STRING'],
})
