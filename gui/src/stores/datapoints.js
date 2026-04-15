import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { dpApi, searchApi, systemApi } from '@/api/client'

const SCROLL_STATE_KEY = 'obs.dp.scroll'

export const useDatapointStore = defineStore('datapoints', () => {
  const items    = ref([])
  const total    = ref(0)
  const loading  = ref(false)
  const datatypes = ref([])  // [{ name, python_type, description }]
  const sortCol  = ref('name')
  const sortDir  = ref('asc')
  const hasMore  = ref(false)

  // Internal cursor — tracks the next page to load in infinite-scroll mode.
  const _nextPage    = ref(0)
  const _lastParams  = ref({})

  // --------------------------------------------------------------------------
  // Core search (replaces the old fetchPage + search pair)
  //
  // params: { q, tag, quality, type }
  // append: false → replace items (new search / filter change / sort change)
  //         true  → append items (scroll triggered "load more")
  // --------------------------------------------------------------------------
  async function search(params = {}, append = false) {
    if (loading.value && append) return   // debounce concurrent scroll triggers

    loading.value = true

    const page = append ? _nextPage.value : 0
    if (!append) {
      _lastParams.value  = { ...params }
      _nextPage.value    = 0
    }

    try {
      const { data } = await searchApi.search({
        ...params,
        sort:  sortCol.value,
        order: sortDir.value,
        page,
        size:  50,
      })

      if (append) {
        items.value = [...items.value, ...data.items]
      } else {
        items.value = data.items
      }

      total.value     = data.total
      _nextPage.value = page + 1
      hasMore.value   = _nextPage.value < data.pages
    } finally {
      loading.value = false
    }
  }

  // Load the next page in infinite-scroll mode.
  async function loadMore() {
    if (!hasMore.value || loading.value) return
    await search(_lastParams.value, true)
  }

  // Sort a column — resets to page 0.
  function setSort(col) {
    if (sortCol.value === col) {
      sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
    } else {
      sortCol.value = col
      sortDir.value = 'asc'
    }
    search(_lastParams.value, false)
  }

  // --------------------------------------------------------------------------
  // CRUD
  // --------------------------------------------------------------------------
  async function create(payload) {
    const { data } = await dpApi.create(payload)
    items.value.unshift(data)
    total.value++
    return data
  }

  async function update(id, payload) {
    const { data } = await dpApi.update(id, payload)
    const idx = items.value.findIndex(d => d.id === id)
    if (idx !== -1) items.value[idx] = data
    return data
  }

  async function remove(id) {
    await dpApi.delete(id)
    items.value = items.value.filter(d => d.id !== id)
    total.value--
  }

  async function loadDatatypes() {
    if (datatypes.value.length) return
    const { data } = await systemApi.datatypes()
    datatypes.value = data
  }

  // Update a single item's live value from WebSocket.
  function patchValue(id, value, quality) {
    const dp = items.value.find(d => d.id === id)
    if (dp) { dp.value = value; dp.quality = quality }
  }

  // --------------------------------------------------------------------------
  // Scroll-state persistence (sessionStorage)
  // Used to restore position when navigating back from a detail view.
  // --------------------------------------------------------------------------
  function saveScrollState(scrollY, filters) {
    try {
      sessionStorage.setItem(SCROLL_STATE_KEY, JSON.stringify({
        scrollY,
        filters,
        count: items.value.length,
      }))
    } catch { /* quota errors are non-fatal */ }
  }

  function restoreScrollState() {
    try {
      const raw = sessionStorage.getItem(SCROLL_STATE_KEY)
      return raw ? JSON.parse(raw) : null
    } catch { return null }
  }

  function clearScrollState() {
    sessionStorage.removeItem(SCROLL_STATE_KEY)
  }

  return {
    items, total, loading, datatypes, sortCol, sortDir, hasMore,
    search, loadMore, setSort,
    create, update, remove, loadDatatypes, patchValue,
    saveScrollState, restoreScrollState, clearScrollState,
  }
})
