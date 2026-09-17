/**
 * Helpers for the JSON/XML extractor blocks in the logic editor (issue #1104).
 *
 * Both blocks expose their configured rows as `out_1 … out_N` ports and ship a
 * `_preview` snapshot of the last received payload in their run outputs. The
 * config panel builds its path picker from that snapshot — these helpers keep
 * the picker alive across runs that carry no payload and let debug views show
 * the configured output names instead of the technical port ids.
 */

const EXTRACTOR_ROW_FIELDS = {
  json_extractor: 'json_paths',
  xml_extractor:  'xml_paths',
}

function parseRows(raw) {
  try {
    const parsed = JSON.parse(raw || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

/**
 * Map `out_N` port ids of an extractor node to the labels the user gave its
 * output rows. Returns an empty object for every other node type.
 *
 * @param {object|null} node   logic node (`{ type, data }`)
 * @param {Function}    [t]    vue-i18n t() — used for the "Wert N" fallback of
 *                             rows without a label; without it the technical
 *                             port id is kept for such rows
 * @returns {Record<string, string>}
 */
export function extractorOutputLabels(node, t) {
  const field = EXTRACTOR_ROW_FIELDS[node?.type]
  if (!field) return {}
  const labels = {}
  parseRows(node.data?.[field]).forEach((entry, i) => {
    const n = i + 1
    const label = String(entry?.label ?? '').trim()
    if (label) labels[`out_${n}`] = label
    else if (t) labels[`out_${n}`] = t('logic.nodeConfig.extractor.valueN', { n })
  })
  return labels
}

function hasPreview(nodeOut) {
  const preview = nodeOut?._preview
  return preview !== null && preview !== undefined && preview !== ''
}

/**
 * Carry the last non-empty `_preview` of every node over into a fresh set of
 * run outputs.
 *
 * Every graph execution re-evaluates the whole sheet, so an extractor whose
 * upstream block did not fire this time (e.g. an untriggered API client after
 * the auto-save that follows adding an output) reports no payload. Without
 * this the path picker would vanish until the next real trigger.
 *
 * @param {Record<string, object>} previous  outputs of the previous run
 * @param {Record<string, object>} outputs   outputs of the current run
 * @returns {Record<string, object>} `outputs` with retained previews merged in
 */
export function retainPreviews(previous, outputs) {
  const merged = { ...(outputs || {}) }
  for (const [nodeId, prevOut] of Object.entries(previous || {})) {
    if (!hasPreview(prevOut) || hasPreview(merged[nodeId])) continue
    const next = merged[nodeId]
    merged[nodeId] = { ...(next && typeof next === 'object' ? next : {}), _preview: prevOut._preview }
  }
  return merged
}
