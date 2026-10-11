// Debug-tab labels of the hems_surplus block: its outputs are addressed by the
// stable consumer id (c_<id>), which means nothing to a user, so they are shown
// under the consumer's name, like the ports on the block card.

const DIAGNOSTICS = ['grid_power', 'surplus', 'budget', 'allocated', 'remaining', 'status', 'warning']

const CONSUMER_MODES = ['percent', 'onoff', 'trigger']

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
    let id = String(c.id || '').replace(/[^A-Za-z0-9_]/g, '') || `n${index + 1}`
    while (seen.has(id)) id = `${id}_`
    seen.add(id)
    consumers.push({ ...c, id, mode: CONSUMER_MODES.includes(c.mode) ? c.mode : 'percent' })
  })
  return consumers
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
    const name = String(c.name ?? '').trim() || t('logic.portLabels.hems.consumer', { n: i + 1 })
    labels[`c_${c.id}`] = name
    labels[`c_${c.id}_status`] = `${name}: ${t('logic.portLabels.hems.status')}`
  })
  for (const key of DIAGNOSTICS) labels[key] = t(`logic.portLabels.hems.${key}`)
  return labels
}
