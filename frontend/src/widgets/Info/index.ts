import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

interface ExtraDatapoint {
  id: string
}

WidgetRegistry.register({
  type: 'Info',
  label: 'Info',
  icon: 'ℹ️',
  group: 'Anzeige',
  minW: 2, minH: 2,
  defaultW: 3, defaultH: 3,
  component: Widget,
  configComponent: Config,
  defaultConfig: { label: '', decimals: 1, unit: '', extra_datapoints: [] },
  compatibleTypes: ['FLOAT', 'INTEGER', 'BOOLEAN', 'STRING'],
  getExtraDatapointIds: (config) => {
    const extras = config.extra_datapoints as ExtraDatapoint[] | undefined
    return (extras ?? []).map((e) => e.id).filter(Boolean)
  },
})
