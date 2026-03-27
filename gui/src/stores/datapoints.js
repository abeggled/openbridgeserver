import { defineStore } from 'pinia'
import { ref } from 'vue'
import { dpApi, searchApi, systemApi } from '@/api/client'

export const useDatapointStore = defineStore('datapoints', () => {
  const items    = ref([])
  const total    = ref(0)
  const page     = ref(0)
  const size     = ref(50)
  const pages    = ref(1)
  const loading  = ref(false)
  const datatypes = ref([])  // [{ name, python_type, description }]

  async function fetchPage(p = 0, s = 50) {
    loading.value = true
    try {
      const { data } = await dpApi.list(p, s)
      items.value = data.items
      total.value = data.total
      page.value  = data.page
      size.value  = data.size
      pages.value = data.pages
    } finally {
      loading.value = false
    }
  }

  async function search(params) {
    loading.value = true
    try {
      const { data } = await searchApi.search({ ...params, size: params.size ?? 50, page: params.page ?? 0 })
      items.value = data.items
      total.value = data.total
      page.value  = data.page
      size.value  = data.size
      pages.value = data.pages
    } finally {
      loading.value = false
    }
  }

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

  // Update a single item's live value from WebSocket
  function patchValue(id, value, quality) {
    const dp = items.value.find(d => d.id === id)
    if (dp) { dp.value = value; dp.quality = quality }
  }

  return {
    items, total, page, size, pages, loading, datatypes,
    fetchPage, search, create, update, remove, loadDatatypes, patchValue,
  }
})
