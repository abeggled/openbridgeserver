// Debug-tab labels of the hems_surplus block: its outputs are addressed by the
// stable consumer id (c_<id>), which means nothing to a user, so they are shown
// under the consumer's name, like the ports on the block card.

const DIAGNOSTICS = ['grid_power', 'surplus', 'budget', 'allocated', 'remaining', 'status', 'warning']

function parseConsumers(raw) {
  let list = raw
  if (typeof raw === 'string') {
    try { list = JSON.parse(raw || '[]') } catch (_) { list = [] }
  }
  return Array.isArray(list) ? list.filter(c => c && typeof c === 'object' && !Array.isArray(c)) : []
}

/**
 * @param {{type?: string, data?: object}} node  logic node
 * @param {(key: string, params?: object) => string} t  vue-i18n translate function
 * @returns {Record<string, string>} output handle → display label
 */
export function hemsOutputLabels(node, t) {
  if (node?.type !== 'hems_surplus') return {}
  const labels = {}
  parseConsumers(node.data?.consumers).forEach((c, i) => {
    const name = String(c.name ?? '').trim() || t('logic.portLabels.hems.consumer', { n: i + 1 })
    labels[`c_${c.id}`] = name
    labels[`c_${c.id}_status`] = `${name}: ${t('logic.portLabels.hems.status')}`
  })
  for (const key of DIAGNOSTICS) labels[key] = t(`logic.portLabels.hems.${key}`)
  return labels
}
