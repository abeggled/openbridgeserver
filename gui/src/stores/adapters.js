import { defineStore } from 'pinia'
import { ref } from 'vue'
import { adapterApi, systemApi } from '@/api/client'

export const useAdapterStore = defineStore('adapters', () => {
  const adapters = ref([])   // AdapterDetailOut[]
  const loading  = ref(false)

  async function fetchAdapters() {
    loading.value = true
    try {
      const { data } = await systemApi.adapters()
      adapters.value = data
    } finally {
      loading.value = false
    }
  }

  async function testAdapter(type, config) {
    const { data } = await adapterApi.test(type, config)
    return data   // { success, detail }
  }

  async function saveConfig(type, config, enabled = true) {
    const { data } = await adapterApi.updateConfig(type, config, enabled)
    return data
  }

  async function getConfig(type) {
    const { data } = await adapterApi.getConfig(type)
    return data
  }

  async function getSchema(type) {
    const { data } = await adapterApi.schema(type)
    return data
  }

  return { adapters, loading, fetchAdapters, testAdapter, saveConfig, getConfig, getSchema }
})
