import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Kamera',
  label: 'widgets.kamera.title',
  icon: '📷',
  group: 'Medien & Sonstiges',
  minW: 3, minH: 2,
  defaultW: 6, defaultH: 4,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    // 'url' = Kamera-URL (MJPEG/Snapshot/HLS), 'go2rtc' = Kamera-Server GO2RTC/FRIGATE (#1479)
    source: 'url',
    streamInstanceId: '',
    streamCamera: '',
    tileSrc: '',
    fullscreenSrc: '',
    preferMode: 'auto',
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
    clickAction: 'none',
    targetNodeId: '',
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
  // Die UUIDs in der Konfiguration (Kamera-Server-Instanz, Zielseite) sind keine
  // Datenpunkte; ohne diese Angabe hält der Editor sie für fehlende DP-Verweise.
  getExtraDatapointIds: () => [],
})
