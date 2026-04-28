import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Licht',
  label: 'Licht',
  icon: '💡',
  group: 'Steuerung',
  minW: 2, minH: 2,
  defaultW: 3, defaultH: 4,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    mode: 'dimm',
    dp_switch: '',
    dp_switch_status: '',
    dp_dim: '',
    dp_dim_status: '',
    dp_tw: '',
    dp_tw_status: '',
    tw_warm_k: 2700,
    tw_cold_k: 6500,
    dp_r: '', dp_g: '', dp_b: '',
    dp_r_status: '', dp_g_status: '', dp_b_status: '',
    dp_w: '',
    dp_w_status: '',
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
  getExtraDatapointIds: (config) => {
    return [
      config.dp_switch        as string,
      config.dp_switch_status as string,
      config.dp_dim           as string,
      config.dp_dim_status    as string,
      config.dp_tw            as string,
      config.dp_tw_status     as string,
      config.dp_r             as string,
      config.dp_g             as string,
      config.dp_b             as string,
      config.dp_r_status      as string,
      config.dp_g_status      as string,
      config.dp_b_status      as string,
      config.dp_w             as string,
      config.dp_w_status      as string,
    ].filter(Boolean) as string[]
  },
})
