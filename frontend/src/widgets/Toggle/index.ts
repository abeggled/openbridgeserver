import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Toggle',
  label: 'Schalter',
  icon: '🔘',
  minW: 2, minH: 2,
  defaultW: 2, defaultH: 3,
  component: Widget,
  configComponent: Config,
  defaultConfig: { label: '' },
  compatibleTypes: ['BOOLEAN'],
  supportsStatusDatapoint: true,
})
