import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'ButtonGroup',
  label: 'widgets.buttongroup.title',
  icon: '🔳',
  group: 'Steuerung',
  minW: 2, minH: 2,
  defaultW: 4, defaultH: 4,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    columns: 2,
    showLabel: true,
    buttons: [
      {
        id: 'button-1',
        label: 'widgets.buttongroup.defaultButton',
        icon: '',
        color: '#3b82f6',
        value: 'true',
        resetEnabled: false,
        resetValue: 'false',
        resetDelayMs: 300,
      },
    ],
  },
  compatibleTypes: ['BOOLEAN', 'INTEGER'],
  supportsStatusDatapoint: false,
})
