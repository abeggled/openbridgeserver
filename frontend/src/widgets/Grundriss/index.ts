import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

interface MiniWidgetEntry {
  widgetType?: string
  datapointId?: string | null
  statusDatapointId?: string | null
  config?: Record<string, unknown>
}

WidgetRegistry.register({
  type: 'Grundriss',
  label: 'Grundriss / Schema',
  icon: '🗺️',
  group: 'Anzeige',
  minW: 4, minH: 3,
  defaultW: 8, defaultH: 6,
  noDatapoint: true,
  compatibleTypes: ['*'],
  defaultConfig: {
    image:         null,
    imageNaturalW: 1920,
    imageNaturalH: 1080,
    rotation:      0,
    showAreaNames: true,
    areas:         [],
    miniWidgets:   [],
  },
  getExtraDatapointIds: (config) => {
    const mws = (config.miniWidgets as MiniWidgetEntry[] | undefined) ?? []
    const ids: string[] = []
    for (const mw of mws) {
      if (mw.datapointId)       ids.push(mw.datapointId)
      if (mw.statusDatapointId) ids.push(mw.statusDatapointId)
      // Also collect extra IDs from the mini-widget's own config (e.g. Licht with multiple DPs)
      if (mw.widgetType && mw.widgetType !== 'Grundriss' && mw.config) {
        const def = WidgetRegistry.get(mw.widgetType)
        if (def?.getExtraDatapointIds) ids.push(...def.getExtraDatapointIds(mw.config))
      }
    }
    return ids.filter(Boolean) as string[]
  },
  component: Widget,
  configComponent: Config,
})
