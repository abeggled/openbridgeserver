import { defineStore } from 'pinia'
import { ref } from 'vue'
import { navLinksApi } from '@/api/client'

export const useNavLinksStore = defineStore('navLinks', () => {
  const links   = ref([])
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      const { data } = await navLinksApi.list()
      links.value = data
    } catch {
      // non-critical
    } finally {
      loading.value = false
    }
  }

  async function create(payload) {
    const { data } = await navLinksApi.create(payload)
    links.value = [...links.value, data].sort((a, b) => a.sort_order - b.sort_order || 0)
    return data
  }

  async function update(id, payload) {
    const { data } = await navLinksApi.update(id, payload)
    links.value = links.value
      .map(l => l.id === id ? data : l)
      .sort((a, b) => a.sort_order - b.sort_order || 0)
    return data
  }

  async function remove(id) {
    await navLinksApi.delete(id)
    links.value = links.value.filter(l => l.id !== id)
  }

  return { links, loading, load, create, update, remove }
})
