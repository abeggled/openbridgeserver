const LEVEL_NAMES = ['Hierarchiename', 'Erste Ebene', 'Zweite Ebene', 'Dritte Ebene', 'Vierte Ebene']

const GENERIC_LABELS = [
  '0 — Hierarchiename (Standard, z.B. "Gebäude")',
  '1 — Erste Ebene (z.B. "EG" statt "Gebäude")',
  '2 — Zweite Ebene (z.B. "Wohnzimmer")',
  '3 — Dritte Ebene',
  '4 — Vierte Ebene',
]

function collectNamesAtDepth(nodes, depth) {
  if (!nodes || nodes.length === 0) return []
  if (depth === 0) return nodes.map(n => n?.name).filter(Boolean)
  return nodes.flatMap(n => collectNamesAtDepth(n?.children, depth - 1))
}

/**
 * Build the display-depth select options for a hierarchy.
 *
 * @param {object} opts
 * @param {boolean}  opts.isEdit     - true when editing an existing hierarchy
 * @param {object}   opts.tree       - current hierarchy tree (for isEdit mode)
 * @param {Array}    opts.rootNodes  - loaded root nodes (for isEdit mode)
 * @param {number}   [opts.maxDepth] - max depth to show (default 4)
 * @param {Function} [opts.t]        - vue-i18n t() function for translations
 */
export function buildDepthOptions({ isEdit, tree, rootNodes, maxDepth = 4, t }) {
  const tp  = (k)        => t ? t(k)         : null
  const tpn = (k, p)     => t ? t(k, p)      : null
  const options = []

  for (let level = 0; level <= maxDepth; level++) {
    if (!isEdit) {
      const optLabel = tp(`hierarchy.depthOptions.generic${level}`) ?? GENERIC_LABELS[level]
      options.push({ value: level, label: optLabel, disabled: false })
      continue
    }

    if (level === 0) {
      const name       = tree?.name ?? (tp('hierarchy.depthOptions.levelName0') ?? LEVEL_NAMES[0])
      const rootSuffix = tp('hierarchy.depthOptions.rootSuffix') ?? 'Hierarchiename'
      const optLabel   = `0 — ${name} (${rootSuffix})`
      options.push({ value: 0, label: optLabel, disabled: false })
      continue
    }

    const names      = collectNamesAtDepth(rootNodes, level - 1)
    const levelLabel = (level < LEVEL_NAMES.length
      ? tp(`hierarchy.depthOptions.levelName${level}`)
      : tpn('hierarchy.depthOptions.levelFallback', { level }))
      ?? (LEVEL_NAMES[level] ?? `Ebene ${level}`)

    if (names.length === 0) {
      const optLabel = `${level} — ${levelLabel}`
      options.push({ value: level, label: optLabel, disabled: true })
      continue
    }

    const example  = names[0]
    const distinct = new Set(names).size
    const suffix   = distinct === 1
      ? (tpn('hierarchy.depthOptions.only',     { example })                 ?? `(nur "${example}")`)
      : (tpn('hierarchy.depthOptions.examples', { example, count: distinct }) ?? `(z.B. "${example}" — ${distinct} unterschiedliche)`)
    const optLabel = `${level} — ${levelLabel} ${suffix}`
    options.push({ value: level, label: optLabel, disabled: false })
  }

  return options
}
