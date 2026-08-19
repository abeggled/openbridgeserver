import { describe, expect, it } from 'vitest'
import { ref } from 'vue'
import { matchesQuickFilter, useQuickFilter } from '@/composables/useQuickFilter'

describe('useQuickFilter', () => {
  it('matches all normalized query terms across multiple searchable values', () => {
    expect(matchesQuickFilter(['Haus › Gebäude', 'EG › Küche'], 'gebaude kuche')).toBe(true)
    expect(matchesQuickFilter(['Haus › Gebäude', 'EG › Küche'], 'gebaude dach')).toBe(false)
  })

  it('reactively filters items without modifying the source collection', () => {
    const items = ref([
      { id: 'a', name: 'Lüftung', description: 'Dach' },
      { id: 'b', name: 'Heizung', description: 'Keller' },
    ])
    const query = ref('luft dach')
    const filtered = useQuickFilter(items, query, (item) => [item.name, item.description])

    expect(filtered.value.map((item) => item.id)).toEqual(['a'])
    expect(items.value).toHaveLength(2)

    query.value = ''
    expect(filtered.value).toBe(items.value)
  })
})
