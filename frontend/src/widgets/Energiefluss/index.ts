import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

interface EntityConfig {
  id: string
}

WidgetRegistry.register({
  type: 'Energiefluss',
  label: 'Energiefluss',
  icon: '⚡',
  group: 'Anzeige',
  minW: 4, minH: 4,
  defaultW: 6, defaultH: 5,
  component: Widget,
  configComponent: Config,
  defaultConfig: { label: '', entities: [] },
  compatibleTypes: ['FLOAT', 'INTEGER'],
  noDatapoint: true,
  getExtraDatapointIds: (config) => {
    const entities = config.entities as EntityConfig[] | undefined
    const ids = (entities ?? []).map((e) => e.id).filter(Boolean)
    const houseDp = config.house_dp as string | undefined
    if (houseDp) ids.push(houseDp)
    return ids
  },
})
