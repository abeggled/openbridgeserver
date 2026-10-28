// Home Assistant binding mapping helpers (#1392).
// Pure functions: they return locale keys and technical tokens, never
// translated text. The profile table itself comes from the backend
// (GET /adapters/homeassistant/profiles), so only the precedence rules are
// mirrored here — keep them in sync with obs/adapters/homeassistant/profiles.py.

export const HA_MAPPING_KEYS = ['action', 'write_actions', 'state_map', 'transitional_states', 'fault_states']

export function entityDomain(entityId) {
  // HA (and the backend validator) treat entity ids case-insensitively.
  return String(entityId ?? '').trim().toLowerCase().split('.', 1)[0]
}

export function profileLabelKey(profileId) {
  return `adapters.homeassistant.profile.${String(profileId).replace('.', '_')}`
}

export function conventionKey(code) {
  return `adapters.homeassistant.convention.${code}`
}

export function reasonKey(code) {
  return `adapters.homeassistant.reason.${code}`
}

const TYPE_GROUP = { BOOLEAN: 'bool', FLOAT: 'number', INTEGER: 'number', STRING: 'text' }

// { profile, dp } when the profile's value type does not fit the DataPoint, else null.
export function profileTypeMismatch(catalog, profileId, dpDataType, attribute = '') {
  const profile = (catalog ?? []).find(p => p.id === profileId)
  const dp = String(dpDataType ?? '').toUpperCase()
  if (!profile || !(profile.data_type in TYPE_GROUP) || !(dp in TYPE_GROUP)) return null
  // A different attribute replaces the profile's value, and with it the profile's type.
  if (attribute && attribute !== profile.attribute) return null
  return TYPE_GROUP[profile.data_type] === TYPE_GROUP[dp] ? null : { profile: profile.data_type, dp }
}

export function profilesForEntity(catalog, entityId) {
  const domain = entityDomain(entityId)
  return (catalog ?? []).filter(p => p.domains.includes(domain))
}

function hasValue(value) {
  if (value == null || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  // {} counts as unset, as in the backend (state_map / write_actions)
  if (typeof value === 'object') return Object.keys(value).length > 0
  return true
}

function materialize(action, entityId) {
  return { ...action, domain: action.domain || entityDomain(entityId) }
}

// Resolve a binding config against the profile catalog with the backend's precedence:
// legacy service_* > write_actions > action > profile > default.
export function resolveHaMapping(cfg, catalog) {
  const profile = (catalog ?? []).find(p => p.id === cfg.profile) ?? null
  const attribute = cfg.attribute || profile?.attribute || null
  // The profile's read normalization only applies to the value the profile itself reads.
  const ownValue = profile !== null && attribute === profile.attribute
  const read = {
    attribute,
    state_map: hasValue(cfg.state_map) ? cfg.state_map : (ownValue ? profile.state_map ?? null : null),
    transitional_states: hasValue(cfg.transitional_states) ? cfg.transitional_states : (ownValue ? profile.transitional_states ?? [] : []),
    fault_states: hasValue(cfg.fault_states) ? cfg.fault_states : (ownValue ? profile.fault_states ?? [] : []),
  }
  let mode = 'default'
  let write = null
  let writeKind = 'none'  // 'single' action or value-keyed 'map'
  if (cfg.service_domain || cfg.service_name || cfg.service_data_key) {
    mode = 'legacy'
  } else if (hasValue(cfg.write_actions)) {
    mode = 'write_actions'
    write = cfg.write_actions
    writeKind = 'map'
  } else if (hasValue(cfg.action)) {
    mode = 'action'
    write = cfg.action
    writeKind = 'single'
  } else if (profile) {
    mode = 'profile'
    if (profile.action) {
      write = materialize(profile.action, cfg.entity_id)
      writeKind = 'single'
    } else if (profile.write_actions) {
      write = Object.fromEntries(Object.entries(profile.write_actions).map(([k, a]) => [k, materialize(a, cfg.entity_id)]))
      writeKind = 'map'
    }
  }
  return { mode, read, write, writeKind, convention: profile?.convention ?? null }
}

// Flatten a resolved write mapping into display rows: { when, action, valuePath }.
// `when` is null for a single action (every value triggers it). The kind is passed
// explicitly: a value key may itself be called "name" or "mode".
export function writeRows(write, kind) {
  if (!write || kind === 'none') return []
  if (kind === 'single') return [{ when: null, action: `${write.domain}.${write.name}`, valuePath: write.value_path ?? null }]
  return Object.entries(write).map(([when, a]) => ({ when, action: `${a.domain}.${a.name}`, valuePath: a.value_path ?? null }))
}

// The editable advanced mapping as JSON text (only the keys that are set).
export function advancedMappingText(cfg) {
  const out = {}
  for (const key of HA_MAPPING_KEYS) if (hasValue(cfg[key])) out[key] = cfg[key]
  return Object.keys(out).length ? JSON.stringify(out, null, 2) : ''
}

const isObject = v => v !== null && typeof v === 'object' && !Array.isArray(v)
const isAction = a => isObject(a) && typeof a.domain === 'string' && typeof a.name === 'string'
const isStringList = v => Array.isArray(v) && v.every(s => typeof s === 'string')
// The shape each key must have; the form renders these values, so a wrong type is invalid input.
const MAPPING_SHAPES = {
  action: v => v === null || (isObject(v) && (Object.keys(v).length === 0 || isAction(v))),
  write_actions: v => v === null || (isObject(v) && Object.values(v).every(isAction)),
  state_map: v => v === null || isObject(v),
  transitional_states: isStringList,
  fault_states: isStringList,
}

// Parse the advanced JSON. Returns { ok, value } with every mapping key present
// (missing keys reset to their empty value), or { ok: false } on invalid input.
export function parseAdvancedMapping(text) {
  const empty = { action: null, write_actions: null, state_map: null, transitional_states: [], fault_states: [] }
  if (!String(text ?? '').trim()) return { ok: true, value: empty }
  let parsed
  try {
    parsed = JSON.parse(text)
  } catch {
    return { ok: false }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return { ok: false }
  if (Object.keys(parsed).some(k => !HA_MAPPING_KEYS.includes(k))) return { ok: false }
  if (Object.entries(parsed).some(([k, v]) => !MAPPING_SHAPES[k](v))) return { ok: false }
  return { ok: true, value: { ...empty, ...parsed } }
}

// ---------------------------------------------------------------------------
// Suggestion lists for the binding form pickers: [{ value, label, detail }]
// ---------------------------------------------------------------------------

function matches(query, ...texts) {
  const q = String(query ?? '').trim().toLowerCase()
  return !q || texts.some(t => String(t ?? '').toLowerCase().includes(q))
}

export function entitySuggestions(items) {
  return (items ?? []).map(e => ({
    value: e.entity_id,
    label: e.name && e.name !== e.entity_id ? e.name : null,
    detail: [e.state, e.unit].filter(v => v != null && v !== '').join(' ') || null,
  }))
}

export function attributeSuggestions(entity, query) {
  return Object.entries(entity?.attributes ?? {})
    .filter(([key]) => key !== 'friendly_name' && matches(query, key))
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => ({ value: key, label: null, detail: String(value) }))
}

export function domainSuggestions(catalog, query) {
  return (catalog ?? [])
    .filter(d => matches(query, d.domain))
    .map(d => ({ value: d.domain, label: null, detail: null }))
}

function servicesOf(catalog, domain) {
  return (catalog ?? []).find(d => d.domain === domain)?.services ?? []
}

export function serviceSuggestions(catalog, domain, query) {
  return servicesOf(catalog, domain)
    .filter(s => matches(query, s.service, s.name))
    .map(s => ({ value: s.service, label: s.name, detail: null }))
}

export function fieldSuggestions(catalog, domain, service, query) {
  const found = servicesOf(catalog, domain).find(s => s.service === service)
  const fields = found ? found.fields : servicesOf(catalog, domain).flatMap(s => s.fields)
  const seen = new Set()
  return fields
    .filter(f => matches(query, f.key, f.name) && !seen.has(f.key) && seen.add(f.key))
    .map(f => ({ value: f.key, label: f.name, detail: f.required ? '*' : null }))
}
