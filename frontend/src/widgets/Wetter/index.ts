import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'Wetter',
  label: 'Wetter',
  icon: '🌤️',
  minW: 4, minH: 3,
  defaultW: 6, defaultH: 5,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    url: '',
    refreshInterval: 600,
    units: 'metric',
    show_feels_like: true,
    show_humidity: true,
    show_wind: true,
    show_pressure: false,
    show_uvi: false,
    show_clouds: false,
    show_visibility: false,
    show_sunrise_sunset: false,
    show_forecast: true,
    forecast_days: 4,
    show_forecast_precipitation: true,
    show_alerts: true,
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
})
