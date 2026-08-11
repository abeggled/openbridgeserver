import { computed, unref } from 'vue'

export function normalizeQuickFilterText(value) {
  return String(value ?? '')
    .normalize('NFKD')
    .replace(/\p{Diacritic}/gu, '')
    .toLocaleLowerCase()
}

export function matchesQuickFilter(value, query) {
  const terms = normalizeQuickFilterText(query).trim().split(/\s+/).filter(Boolean)
  if (!terms.length) return true
  const values = Array.isArray(value) ? value : [value]
  const searchable = normalizeQuickFilterText(values.flat(Infinity).join(' '))
  return terms.every((term) => searchable.includes(term))
}

export function useQuickFilter(items, query, searchableValue = (item) => item) {
  return computed(() => {
    const source = unref(items) ?? []
    const currentQuery = unref(query)
    if (!String(currentQuery ?? '').trim()) return source
    return source.filter((item) => matchesQuickFilter(searchableValue(item), currentQuery))
  })
}
