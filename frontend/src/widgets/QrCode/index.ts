import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'QrCode',
  label: 'QR-Code',
  icon: '▣',
  group: 'Medien & Sonstiges',
  minW: 2, minH: 2,
  defaultW: 3, defaultH: 3,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    qrType:          'url',
    label:           '',
    errorCorrection: 'M',
    darkColor:       '#000000',
    lightColor:      '#ffffff',
    // URL
    url_url:         '',
    // WiFi
    wifi_ssid:       '',
    wifi_password:   '',
    wifi_encryption: 'WPA',
    wifi_hidden:     false,
    // vCard
    vcard_firstname: '',
    vcard_lastname:  '',
    vcard_company:   '',
    vcard_mobile:    '',
    vcard_email:     '',
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
})
