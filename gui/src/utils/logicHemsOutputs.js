import { isBackendFalse } from '@/utils/logicBooleans'
import { toBackendNumberText } from '@/utils/logicNumbers'
import { trimLikeStrip } from '@/utils/pythonWhitespace'

// Debug-tab labels of the hems_surplus block: its outputs are addressed by the
// stable consumer id (c_<id>), which means nothing to a user, so they are shown
// under the consumer's name, like the ports on the block card.

export const HEMS_DIAGNOSTICS = ['grid_power', 'surplus', 'budget', 'allocated', 'remaining', 'status', 'warning']

const CONSUMER_MODES = ['percent', 'onoff', 'trigger']
const GRID_MODES = ['bidirectional', 'split', 'feed_in_only']

// An enum setting as the engine reads it (`_enum` in hems_surplus.py):
// text compared case- and whitespace-insensitively.
export function hemsEnum(value) {
  return typeof value === 'string' ? trimLikeStrip(value).toLowerCase() : value
}

/**
 * The configured measurement mode, read like the engine (`grid_mode`).
 *
 * @param {object} data  node data
 * @returns {'bidirectional'|'split'|'feed_in_only'}
 */
export function hemsGridMode(data) {
  const mode = hemsEnum(data?.grid_mode)
  return GRID_MODES.includes(mode) ? mode : 'bidirectional'
}
// Mirrors _RESERVED_ID_ENDINGS in hems_surplus.py: an id ending in a port suffix
// would take another consumer's port.
const RESERVED_ID_ENDINGS = ['_status', '_power', '_max', '_reset']

/**
 * The consumer list exactly as the engine reads it (obs/logic/hems_surplus.py
 * `load_consumers`): ids stripped to [A-Za-z0-9_], a positional fallback id,
 * duplicates suffixed with "_" and unknown modes read as "percent". The ports
 * must be built from these ids — an imported or API-created graph may carry
 * raw ids the engine would never emit under.
 *
 * @param {unknown} raw  stored consumer list (array or JSON string)
 * @returns {Array<object>} consumers with normalised `id` and `mode`
 */
export function hemsConsumers(raw) {
  let list = raw
  if (typeof raw === 'string') {
    try { list = JSON.parse(raw || '[]') } catch (_) { list = [] }
  }
  if (!Array.isArray(list)) return []
  const seen = new Set()
  const consumers = []
  list.forEach((c, index) => {
    if (!c || typeof c !== 'object' || Array.isArray(c)) return
    // Only text, finite numbers and booleans count as an id (like _raw_id).
    const usable = typeof c.id === 'string' || typeof c.id === 'boolean' || (typeof c.id === 'number' && Number.isFinite(c.id))
    let id = String((usable && c.id) || '').replace(/[^A-Za-z0-9_]/g, '') || `n${index + 1}`
    if (RESERVED_ID_ENDINGS.some(end => id.endsWith(end))) id = `${id}_`
    while (seen.has(id)) id = `${id}_`
    seen.add(id)
    const mode = hemsEnum(c.mode)
    consumers.push({
      ...c,
      id,
      mode: CONSUMER_MODES.includes(mode) ? mode : 'percent',
      power_source: hemsEnum(c.power_source) === 'input' ? 'input' : c.power_source,
    })
  })
  return consumers
}

/**
 * A yes/no setting as the engine reads it: unset means yes, anything else the
 * backend boolean rule (`to_bool` in obs/logic/booleans.py, used by the engine and GraphExecutor).
 *
 * @param {unknown} value  stored setting
 * @returns {boolean}
 */
export function hemsFlag(value) {
  if (value === undefined || value === null) return true
  return !isBackendFalse(value)
}

/**
 * Whether the meter age check is on, read like the engine reads max_age_s
 * (`_cfg_num`: Python float(), a boolean is no number).
 *
 * @param {unknown} value  stored max_age_s
 * @returns {boolean}
 */
export function hemsAgeCheckActive(value) {
  if (typeof value === 'boolean') return false
  return Number(toBackendNumberText(value, '0')) > 0
}

/**
 * Display name of a consumer: its trimmed name, else "Consumer <n>".
 *
 * @param {{name?: unknown}} consumer
 * @param {number} index  position in the list
 * @param {(key: string, params?: object) => string} t  vue-i18n translate function
 * @returns {string}
 */
export function hemsConsumerName(consumer, index, t) {
  return String(consumer.name ?? '').trim() || t('logic.portLabels.hems.consumer', { n: index + 1 })
}

/**
 * @param {{type?: string, data?: object}} node  logic node
 * @param {(key: string, params?: object) => string} t  vue-i18n translate function
 * @returns {Record<string, string>} output handle → display label
 */
export function hemsOutputLabels(node, t) {
  if (node?.type !== 'hems_surplus') return {}
  const labels = {}
  hemsConsumers(node.data?.consumers).forEach((c, i) => {
    const name = hemsConsumerName(c, i, t)
    labels[`c_${c.id}`] = name
    labels[`c_${c.id}_status`] = `${name}: ${t('logic.portLabels.hems.status')}`
  })
  for (const key of HEMS_DIAGNOSTICS) labels[key] = t(`logic.portLabels.hems.${key}`)
  return labels
}
