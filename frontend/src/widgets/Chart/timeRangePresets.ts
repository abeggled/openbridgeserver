export interface TimeRangePreset {
  value: string
  label: string
}

export const TIME_RANGE_PRESETS: TimeRangePreset[] = [
  { value: 'last_5m',    label: 'Letzte 5 Minuten' },
  { value: 'last_15m',   label: 'Letzte 15 Minuten' },
  { value: 'last_30m',   label: 'Letzte 30 Minuten' },
  { value: 'last_1h',    label: 'Letzte 1 Stunde' },
  { value: 'last_3h',    label: 'Letzte 3 Stunden' },
  { value: 'last_6h',    label: 'Letzte 6 Stunden' },
  { value: 'last_12h',   label: 'Letzte 12 Stunden' },
  { value: 'last_24h',   label: 'Letzte 24 Stunden' },
  { value: 'last_2d',    label: 'Letzte 2 Tage' },
  { value: 'last_7d',    label: 'Letzte 7 Tage' },
  { value: 'last_30d',   label: 'Letzte 30 Tage' },
  { value: 'last_90d',   label: 'Letzte 90 Tage' },
  { value: 'today',      label: 'Heute bis jetzt' },
  { value: 'this_week',  label: 'Diese Woche bis jetzt' },
  { value: 'this_month', label: 'Diesen Monat bis jetzt' },
  { value: 'yesterday',  label: 'Gestern' },
  { value: 'last_week',  label: 'Letzte Woche' },
  { value: 'last_month', label: 'Letzter Monat' },
]

export const DEFAULT_TIME_RANGE = 'last_24h'

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
    case 'last_2d':  return { from: new Date(now.getTime() -  2 * 86_400_000), to: now }
    case 'last_7d':  return { from: new Date(now.getTime() -  7 * 86_400_000), to: now }
    case 'last_30d': return { from: new Date(now.getTime() - 30 * 86_400_000), to: now }
    case 'last_90d': return { from: new Date(now.getTime() - 90 * 86_400_000), to: now }

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
      return { from: new Date(now.getTime() - 24 * 3_600_000), to: now }
  }
}
