import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type:     'RTR',
  label:    'Raumtemperaturregler',
  icon:     '🌡️',
  minW:     3,
  minH:     4,
  defaultW: 3,
  defaultH: 5,
  component:       Widget,
  configComponent: Config,
  defaultConfig: {
    label:             '',
    color:             '#ef4444',
    min_temp:          5,
    max_temp:          35,
    step:              0.5,
    decimals:          1,
    setpoint_offset:   0,
    actual_offset:     0,
    actual_temp_dp_id: null,
    mode_dp_id:        null,
    show_modes:        true,
    supported_modes:   [0, 1, 3, 6],
  },
  compatibleTypes:         ['FLOAT', 'INTEGER'],
  supportsStatusDatapoint: true,
  getExtraDatapointIds: (config) => {
    const ids: string[] = []
    if (config.actual_temp_dp_id) ids.push(config.actual_temp_dp_id as string)
    if (config.mode_dp_id)        ids.push(config.mode_dp_id as string)
    return ids
  },
})
