import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'IFrame',
  label: 'iFrame',
  icon: '🖼️',
  group: 'Medien & Sonstiges',
  minW: 3, minH: 2,
  defaultW: 6, defaultH: 4,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    url: '',
    sandbox: 'allow-same-origin allow-scripts allow-popups allow-forms',
    allowFullscreen: false,
    aspectRatio: '16/9',
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
})
