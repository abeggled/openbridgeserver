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

export function buildDepthOptions({ isEdit, tree, rootNodes, maxDepth = 4 }) {
  const options = []

  for (let level = 0; level <= maxDepth; level++) {
    if (!isEdit) {
      options.push({ value: level, label: GENERIC_LABELS[level], disabled: false })
      continue
    }

    if (level === 0) {
      const name = tree?.name ?? LEVEL_NAMES[0]
      options.push({ value: 0, label: `0 — ${name} (Hierarchiename)`, disabled: false })
      continue
    }

    const names = collectNamesAtDepth(rootNodes, level - 1)
    const levelLabel = LEVEL_NAMES[level] ?? `Ebene ${level}`

    if (names.length === 0) {
      options.push({ value: level, label: `${level} — ${levelLabel}`, disabled: true })
      continue
    }

    const example = names[0]
    const distinct = new Set(names).size
    const suffix = distinct === 1
      ? `(nur "${example}")`
      : `(z.B. "${example}" — ${distinct} unterschiedliche)`
    options.push({ value: level, label: `${level} — ${levelLabel} ${suffix}`, disabled: false })
  }

  return options
}
