import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'ValueDisplay',
  label: 'Wertanzeige',
  icon: '🔢',
  group: 'Anzeige',
  minW: 2, minH: 2,
  defaultW: 3, defaultH: 2,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    mode: 'value',
    rules: [
      {
        fn: 'default',
        threshold: '',
        icon: '',
        color: '#6b7280',
        output_type: 'value',
        calculation: '',
        prefix: '',
        text: '',
        decimals: 1,
        postfix: '',
      },
    ],
  },
  compatibleTypes: ['FLOAT', 'INTEGER', 'BOOLEAN', 'STRING'],
  getExtraDatapointIds: (config) => {
    const id = config.secondary_dp_id as string | undefined
    return id ? [id] : []
  },
})
