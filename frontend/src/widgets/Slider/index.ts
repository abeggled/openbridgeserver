import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Slider',
  label: 'Schieberegler',
  icon: '🎚️',
  group: 'Steuerung',
  minW: 3, minH: 2,
  defaultW: 4, defaultH: 2,
  component: Widget,
  configComponent: Config,
  defaultConfig: { label: '', min: 0, max: 100, step: 1 },
  compatibleTypes: ['FLOAT', 'INTEGER'],
  supportsStatusDatapoint: true,
})
