// 'y' = linke Achse, 'y1' = rechte Achse (Chart.js Achsen-IDs)
export interface SeriesDef {
  id: string
  label: string
  color: string
  axis: 'y' | 'y1'
}

export const COLORS = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#f97316']

interface ExtraSeriesConfig {
  dp_id?: string
  label?: string
  color?: string
  axis?: string
}

/**
 * Baut die Liste der zu zeichnenden Serien aus der Widget-Config.
 *
 * Die Primär-Serie (gebunden über das Top-Level-Datapoint des Widgets) nutzt
 * ein eigenes `primary_label`, falls konfiguriert — fällt sonst auf den
 * Chart-Titel (`widgetLabel`) zurück, damit bestehende Chart-Widgets ohne
 * `primary_label` unverändert funktionieren.
 */
export function buildSeriesDefs(
  config: Record<string, unknown>,
  datapointId: string | null,
  widgetLabel: string,
): SeriesDef[] {
  const result: SeriesDef[] = []

  const primaryColor = (config.primary_color as string | undefined) ?? COLORS[0]
  const primaryAxis  = (config.primary_axis  as string | undefined) === 'right' ? 'y1' : 'y'
  const primaryLabel = (config.primary_label as string | undefined)?.trim() || widgetLabel

  if (datapointId) {
    result.push({ id: datapointId, label: primaryLabel, color: primaryColor, axis: primaryAxis })
  }

  const extra = (config.series as ExtraSeriesConfig[] | undefined) ?? []

  for (const s of extra) {
    if (!s.dp_id) continue
    result.push({
      id:    s.dp_id,
      label: s.label ?? '',
      color: s.color ?? COLORS[result.length % COLORS.length],
      axis:  s.axis === 'right' ? 'y1' : 'y',
    })
  }

  return result
}
