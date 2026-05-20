/**
 * useSetColors — auto-contrast row colouring for ring-buffer entries (#437).
 *
 * Ring-buffer entries that come back from the multi-filterset query carry a
 * `matched_set_ids: list[str]` field. This composable resolves the first
 * matching topbar set (in ascending `topbar_order`) and returns an inline
 * style object with the set colour as background plus an automatically
 * contrasted text colour.
 *
 * Text colour is picked by **WCAG contrast ratio** (chroma.contrast) — for
 * every candidate the one with the higher contrast wins. This replaces the
 * earlier luminance>0.5 threshold which picked white text on mid-tone
 * palette colours (blue-500, emerald, amber, …) where dark text actually
 * scores 5–7:1 and white only 2.5–3.5:1 — i.e. the row text looked washed
 * out on the very colours users most often pick.
 *
 * Module-level cache (`topbarSets`) is intentionally shared between calls so
 * any component can read the same colour map. Use `refreshSets()` after a
 * topbar change (chip add/remove/reorder) to repopulate the cache from
 * `ringbufferApi.listFiltersets()`. Tests can short-circuit the network call
 * via `setSets()`.
 *
 * Tie-break: the first set in `topbar_order` that appears in `matchedIds`
 * wins. Sets that are not `topbar_active` are ignored.
 *
 * Robustness: invalid / null / empty colours and unknown ids cause
 * `getRowStyle` to return `undefined` so the table falls back to its default
 * (hover-only) styling.
 */
import { ref } from 'vue'
import chroma from 'chroma-js'
import { ringbufferApi } from '@/api/client'

const DARK_TEXT = '#0f172a'
const LIGHT_TEXT = '#ffffff'

// Module-level cache — shared across all callers of useSetColors().
// Keys are set ids, values are the full set objects (only sets that are
// topbar-active are stored).
const topbarSets = ref(new Map())

function isValidColor(value) {
  if (value === null || value === undefined) return false
  const trimmed = String(value).trim()
  if (!trimmed) return false
  try {
    return chroma.valid(trimmed)
  } catch {
    return false
  }
}

function getAccentText(color) {
  if (!isValidColor(color)) return DARK_TEXT
  try {
    const cWhite = chroma.contrast(color, LIGHT_TEXT)
    const cDark = chroma.contrast(color, DARK_TEXT)
    return cWhite > cDark ? LIGHT_TEXT : DARK_TEXT
  } catch {
    return DARK_TEXT
  }
}

function setSets(sets) {
  const next = new Map()
  for (const set of Array.isArray(sets) ? sets : []) {
    if (!set || !set.id) continue
    if (!set.topbar_active) continue
    next.set(set.id, set)
  }
  topbarSets.value = next
}

async function refreshSets() {
  try {
    const { data } = await ringbufferApi.listFiltersets()
    setSets(Array.isArray(data) ? data : [])
  } catch {
    // Best-effort — leave whatever was previously cached untouched on error
    // would mask reload failures, so we explicitly empty the cache for an
    // unrecoverable load (matches the topbar-chip behaviour in #435).
    topbarSets.value = new Map()
  }
}

function getRowStyle(matchedIds) {
  if (!Array.isArray(matchedIds) || matchedIds.length === 0) return undefined

  const matchedSet = new Set(matchedIds.filter((v) => typeof v === 'string' && v))
  if (matchedSet.size === 0) return undefined

  // Walk the topbar in order, take the first set that is in the matched list.
  const orderedSets = Array.from(topbarSets.value.values()).sort(
    (a, b) => (a.topbar_order ?? 0) - (b.topbar_order ?? 0),
  )
  for (const set of orderedSets) {
    if (!matchedSet.has(set.id)) continue
    if (!isValidColor(set.color)) return undefined
    return {
      backgroundColor: set.color,
      color: getAccentText(set.color),
    }
  }
  return undefined
}

export function useSetColors() {
  return {
    getRowStyle,
    getAccentText,
    refreshSets,
    setSets,
    sets: topbarSets,
  }
}

// Test-only export so module-level state can be reset between specs.
export function __resetSetColorsCache() {
  topbarSets.value = new Map()
}
