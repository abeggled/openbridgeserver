import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Toggle',
  label: 'Schalter',
  icon: '🔘',
  group: 'Steuerung',
  minW: 2, minH: 2,
  defaultW: 2, defaultH: 3,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    mode: 'switch',
    on:  { icon: '', color: '#3b82f6', text: 'EIN' },
    off: { icon: '', color: '#6b7280', text: 'AUS' },
  },
  compatibleTypes: ['BOOLEAN'],
  supportsStatusDatapoint: true,
})
