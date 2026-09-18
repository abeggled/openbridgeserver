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
 * Number of configured output rows of an extractor node (0 for other node
 * types). Adding or removing a row shifts the `out_N` numbering, so cached
 * per-port values no longer belong to the rows they would be labelled with.
 *
 * @param {object|null} node
 * @returns {number}
 */
export function extractorRowCount(node) {
  const field = EXTRACTOR_ROW_FIELDS[node?.type]
  return field ? parseRows(node.data?.[field]).length : 0
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

/**
 * Parse an extractor `_preview` snapshot. Mirrors the executor: a document
 * that was JSON-encoded twice decodes to a string first — unwrap one such
 * level so the path picker can still list the keys inside. Throws like
 * `JSON.parse` when the snapshot itself is not JSON.
 *
 * @param {string} preview  `_preview` text of a json_extractor run output
 * @returns {unknown} decoded value
 */
export function parseExtractorJson(preview) {
  const obj = JSON.parse(preview)
  if (typeof obj !== 'string') return obj
  try {
    const inner = JSON.parse(obj)
    return inner !== null && typeof inner === 'object' ? inner : obj
  } catch {
    return obj
  }
}

/** Upper bound for the path picker's option list (issue #1104). */
export const EXTRACTOR_MAX_PATHS = 5000

/**
 * Flatten every leaf of a JSON value into dot/bracket notation paths
 * (`sensors[0].temperature`), descending at most 6 levels — a deeper
 * container is listed as a path itself.
 *
 * The list is bounded: a 256 KB document can hold far more leaves than a
 * `<select>` can sensibly show, and collecting them with a spread-push
 * would exceed the call-argument limit for large flat arrays.
 *
 * @param {unknown} obj      decoded document
 * @param {number}  [limit]  maximum number of paths to collect
 * @returns {string[]} paths, at most `limit` entries
 */
export function flattenJsonPaths(obj, limit = EXTRACTOR_MAX_PATHS) {
  const paths = []
  const walk = (value, prefix, depth) => {
    if (paths.length >= limit) return
    if (depth > 6 || value === null || typeof value !== 'object') {
      if (prefix) paths.push(prefix)
      return
    }
    const entries = Array.isArray(value)
      ? value.map((item, i) => [`${prefix}[${i}]`, item])
      : Object.entries(value).map(([k, v]) => [prefix ? `${prefix}.${k}` : k, v])
    for (const [key, child] of entries) {
      if (paths.length >= limit) break
      if (child !== null && typeof child === 'object') walk(child, key, depth + 1)
      else paths.push(key)
    }
  }
  walk(obj, '', 0)
  return paths
}

// Only null/undefined mean "nothing arrived this run" — an empty string is a
// received (empty) document and must replace the retained one.
function hasPreview(nodeOut) {
  const preview = nodeOut?._preview
  return preview !== null && preview !== undefined
}

/**
 * Carry the last received `_preview` of every node over into a fresh set of
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
    const retained = { ...(next && typeof next === 'object' ? next : {}), _preview: prevOut._preview }
    delete retained._preview_pruned
    if (prevOut._preview_pruned === true) retained._preview_pruned = true
    merged[nodeId] = retained
  }
  return merged
}
