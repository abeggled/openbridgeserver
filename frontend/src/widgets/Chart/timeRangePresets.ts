export interface TimeRangePreset {
  value: string
  label: string
}

export const TIME_RANGE_PRESETS: TimeRangePreset[] = [
  { value: 'last_5m',    label: 'widgets.chart.timeRange.last_5m' },
  { value: 'last_15m',   label: 'widgets.chart.timeRange.last_15m' },
  { value: 'last_30m',   label: 'widgets.chart.timeRange.last_30m' },
  { value: 'last_1h',    label: 'widgets.chart.timeRange.last_1h' },
  { value: 'last_3h',    label: 'widgets.chart.timeRange.last_3h' },
  { value: 'last_6h',    label: 'widgets.chart.timeRange.last_6h' },
  { value: 'last_12h',   label: 'widgets.chart.timeRange.last_12h' },
  { value: 'last_24h',   label: 'widgets.chart.timeRange.last_24h' },
  { value: 'last_2d',    label: 'widgets.chart.timeRange.last_2d' },
  { value: 'last_7d',    label: 'widgets.chart.timeRange.last_7d' },
  { value: 'last_30d',   label: 'widgets.chart.timeRange.last_30d' },
  { value: 'last_90d',   label: 'widgets.chart.timeRange.last_90d' },
  { value: 'today',      label: 'widgets.chart.timeRange.today' },
  { value: 'this_week',  label: 'widgets.chart.timeRange.this_week' },
  { value: 'this_month', label: 'widgets.chart.timeRange.this_month' },
  { value: 'yesterday',  label: 'widgets.chart.timeRange.yesterday' },
  { value: 'last_week',  label: 'widgets.chart.timeRange.last_week' },
  { value: 'last_month', label: 'widgets.chart.timeRange.last_month' },
]

export const DEFAULT_TIME_RANGE = 'last_7d'
export const DEFAULT_HISTORY_QUERY_LIMIT = 10000

export type HistoryAggregateInterval = '1m' | '5m' | '15m' | '30m' | '1h' | '6h' | '12h' | '1d'

export type HistoryRequestPlan =
  | { mode: 'raw'; limit: number }
  | { mode: 'aggregate'; fn: 'avg'; interval: HistoryAggregateInterval }

export const DAY_MS = 24 * 60 * 60_000

export function historyRequestPlanForRange(from: Date, to: Date): HistoryRequestPlan {
  const durationMs = to.getTime() - from.getTime()
  if (!Number.isFinite(durationMs) || durationMs <= DAY_MS) {
    return { mode: 'raw', limit: DEFAULT_HISTORY_QUERY_LIMIT }
  }

  // Use intervals >= 1h for multi-day views so all history backends can compress before transport.
  if (durationMs <= 7 * DAY_MS) return { mode: 'aggregate', fn: 'avg', interval: '1h' }
  if (durationMs <= 31 * DAY_MS) return { mode: 'aggregate', fn: 'avg', interval: '6h' }
  if (durationMs <= 90 * DAY_MS) return { mode: 'aggregate', fn: 'avg', interval: '12h' }
  return { mode: 'aggregate', fn: 'avg', interval: '1d' }
}

export function resolveTimeRange(preset: string): { from: Date; to: Date } {
  const now = new Date()

  switch (preset) {
    case 'last_5m':  return { from: new Date(now.getTime() -  5 * 60_000), to: now }
    case 'last_15m': return { from: new Date(now.getTime() - 15 * 60_000), to: now }
    case 'last_30m': return { from: new Date(now.getTime() - 30 * 60_000), to: now }
    case 'last_1h':  return { from: new Date(now.getTime() -  1 * 3_600_000), to: now }
    case 'last_3h':  return { from: new Date(now.getTime() -  3 * 3_600_000), to: now }
    case 'last_6h':  return { from: new Date(now.getTime() -  6 * 3_600_000), to: now }
    case 'last_12h': return { from: new Date(now.getTime() - 12 * 3_600_000), to: now }
    case 'last_24h': return { from: new Date(now.getTime() - 24 * 3_600_000), to: now }
    case 'last_2d':  return { from: new Date(now.getTime() -  2 * DAY_MS), to: now }
    case 'last_7d':  return { from: new Date(now.getTime() -  7 * DAY_MS), to: now }
    case 'last_30d': return { from: new Date(now.getTime() - 30 * DAY_MS), to: now }
    case 'last_90d': return { from: new Date(now.getTime() - 90 * DAY_MS), to: now }

    case 'today': {
      const from = new Date(now)
      from.setHours(0, 0, 0, 0)
      return { from, to: now }
    }

    case 'this_week': {
      // Week starts on Monday (ISO 8601)
      const from = new Date(now)
      const daysFromMonday = (now.getDay() + 6) % 7
      from.setDate(from.getDate() - daysFromMonday)
      from.setHours(0, 0, 0, 0)
      return { from, to: now }
    }

    case 'this_month': {
      const from = new Date(now.getFullYear(), now.getMonth(), 1)
      return { from, to: now }
    }

    case 'yesterday': {
      const from = new Date(now)
      from.setDate(from.getDate() - 1)
      from.setHours(0, 0, 0, 0)
      const to = new Date(from)
      to.setHours(23, 59, 59, 999)
      return { from, to }
    }

    case 'last_week': {
      // Previous full Monday–Sunday week
      const daysFromMonday = (now.getDay() + 6) % 7
      const thisMonday = new Date(now)
      thisMonday.setDate(thisMonday.getDate() - daysFromMonday)
      thisMonday.setHours(0, 0, 0, 0)
      const from = new Date(thisMonday)
      from.setDate(from.getDate() - 7)
      const to = new Date(thisMonday)
      to.setDate(to.getDate() - 1)
      to.setHours(23, 59, 59, 999)
      return { from, to }
    }

    case 'last_month': {
      const from = new Date(now.getFullYear(), now.getMonth() - 1, 1)
      const to   = new Date(now.getFullYear(), now.getMonth(), 0, 23, 59, 59, 999)
      return { from, to }
    }

    default:
      return { from: new Date(now.getTime() - 7 * DAY_MS), to: now }
  }
}
