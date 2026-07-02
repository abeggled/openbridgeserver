import { WidgetRegistry } from '@/widgets/registry'
import Widget from './Widget.vue'
import Config from './Config.vue'

WidgetRegistry.register({
  type: 'MessageArchive',
  label: 'widgets.messageArchive.title',
  icon: '▦',
  group: 'Anzeige',
  minW: 4, minH: 3,
  defaultW: 6, defaultH: 5,
  component: Widget,
  configComponent: Config,
  defaultConfig: {
    archive_ids: [],
    limit: 25,
    severity: [],
    status: [],
    type: [],
    source: [],
    show_archive: true,
    show_source: true,
    allow_read: true,
    allow_acknowledge: true,
  },
  compatibleTypes: ['*'],
  noDatapoint: true,
})
