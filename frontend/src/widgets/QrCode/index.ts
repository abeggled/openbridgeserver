import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'QrCode',
  label: 'QR-Code',
  icon: '▣',
  minW: 2, minH: 2,
  defaultW: 3, defaultH: 3,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    content: '',
    label: '',
    errorCorrection: 'M',
    darkColor: '#000000',
    lightColor: '#ffffff',
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
})
