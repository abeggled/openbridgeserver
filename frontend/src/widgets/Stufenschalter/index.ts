import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Stufenschalter',
  label: 'Stufenschalter',
  icon: '📶',
  group: 'Steuerung',
  minW: 2, minH: 2,
  defaultW: 4, defaultH: 3,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    steps: [
      { label: 'Stufe 1', value: 0, icon: '', color: '#6b7280' },
      { label: 'Stufe 2', value: 1, icon: '', color: '#3b82f6' },
      { label: 'Stufe 3', value: 2, icon: '', color: '#10b981' },
    ],
  },
  compatibleTypes: ['INTEGER', 'FLOAT'],
  supportsStatusDatapoint: true,
})
