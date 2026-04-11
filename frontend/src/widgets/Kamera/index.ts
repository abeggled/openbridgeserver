import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Kamera',
  label: 'Kamera',
  icon: '📷',
  minW: 3, minH: 2,
  defaultW: 6, defaultH: 4,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    url: '',
    streamType: 'mjpeg',
    authType: 'none',
    username: '',
    password: '',
    apiKeyParam: 'token',
    apiKeyValue: '',
    refreshInterval: 5,
    aspectRatio: '16/9',
    objectFit: 'contain',
    useProxy: false,
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
})
