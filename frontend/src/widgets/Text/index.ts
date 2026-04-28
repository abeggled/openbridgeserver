import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Text',
  label: 'Text',
  icon: '📝',
  group: 'Medien & Sonstiges',
  minW: 2, minH: 1,
  defaultW: 4, defaultH: 3,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    content: '',
    align: 'left',
    fontSize: 'base',
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
})
