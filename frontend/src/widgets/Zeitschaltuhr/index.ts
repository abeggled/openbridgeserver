import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Zeitschaltuhr',
  label: 'Zeitschaltuhr',
  icon: '🕐',
  group: 'Steuerung',
  minW: 2, minH: 2,
  defaultW: 3, defaultH: 2,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    instance_id: '',
    datapoint_id: '',
    mode: 'full',
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
  getExtraDatapointIds: (config) => {
    const id = config.datapoint_id as string | undefined
    return id ? [id] : []
  },
})
