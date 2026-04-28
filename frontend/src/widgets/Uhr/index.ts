import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type:    'Uhr',
  label:   'Uhr',
  icon:    '🕐',
  group:   'Anzeige',
  minW:    2,
  minH:    2,
  defaultW: 3,
  defaultH: 3,
  component:       Widget,
  configComponent: Config,
  defaultConfig: {
    mode:        'digital',
    showSeconds: false,
    showDate:    false,
    color:       '#3b82f6',
    label:       '',
    timezone:    '',
  },
  compatibleTypes: ['*'],
  noDatapoint:     true,
})
