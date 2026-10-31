import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'
import { DEFAULT_DATE_PATTERN, sourceIds } from './config'
import { DEFAULT_DAYS } from './calendarEvents'

WidgetRegistry.register({
  type: 'Terminliste',
  label: 'widgets.terminliste.title',
  icon: '📅',
  group: 'Anzeige',
  minW: 3, minH: 2,
  defaultW: 6, defaultH: 4,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    label: '',
    sources: [],
    days: DEFAULT_DAYS,
    separator: 'semicolon',
    show_time: true,
    show_location: false,
    show_empty_days: false,
    date_pattern: DEFAULT_DATE_PATTERN,
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
  getExtraDatapointIds: sourceIds,
})
