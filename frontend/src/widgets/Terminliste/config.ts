/** Shared configuration shape of the Terminliste widget (issue #999). */

export interface CalendarSource {
  /** Named `datapoint_id` so the backend grants anonymous/PIN pages read access to it. */
  datapoint_id: string
  label: string
  color: string
}

export const SEPARATORS = {
  semicolon: '; ',
  star: ' * ',
  newline: '',
} as const

export type SeparatorKey = keyof typeof SEPARATORS

export const DEFAULT_DATE_PATTERN = 'EEE dd.MM.yy'

export const MAX_SOURCES = 8

/** Default colours for new sources, in the order they are added. */
export const SOURCE_COLORS = ['#22c55e', '#f97316', '#3b82f6', '#a855f7', '#ef4444', '#eab308', '#14b8a6', '#ec4899']

export function sourceIds(config: Record<string, unknown>): string[] {
  const sources = config.sources as Partial<CalendarSource>[] | undefined
  return (sources ?? []).map((s) => s?.datapoint_id ?? '').filter(Boolean)
}
