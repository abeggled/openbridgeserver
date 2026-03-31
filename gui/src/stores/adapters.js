import { defineStore } from 'pinia'
import { ref } from 'vue'
import { adapterApi } from '@/api/client'

export const useAdapterStore = defineStore('adapters', () => {
  const instances = ref([])   // AdapterInstanceOut[]
  const loading   = ref(false)

  async function fetchAdapters() {
    loading.value = true
    try {
      const { data } = await adapterApi.listInstances()
      instances.value = data
    } finally {
      loading.value = false
    }
  }

  async function createInstance(adapter_type, name, config, enabled = true) {
    const { data } = await adapterApi.createInstance({ adapter_type, name, config, enabled })
    instances.value.push(data)
    return data
  }

  async function updateInstance(id, payload) {
    const { data } = await adapterApi.updateInstance(id, payload)
    const idx = instances.value.findIndex(a => a.id === id)
    if (idx !== -1) instances.value[idx] = data
    return data
  }

  async function deleteInstance(id) {
    await adapterApi.deleteInstance(id)
    instances.value = instances.value.filter(a => a.id !== id)
  }

  async function testInstance(id, config) {
    const { data } = await adapterApi.testInstance(id, config)
    return data   // { success, detail }
  }

  async function restartInstance(id) {
    const { data } = await adapterApi.restartInstance(id)
    const idx = instances.value.findIndex(a => a.id === id)
    if (idx !== -1) instances.value[idx] = data
    return data
  }

  async function getSchema(type) {
    const { data } = await adapterApi.schema(type)
    return data
  }

  // Alle registrierten Adapter-Typen (für "Neue Instanz" Dropdown)
  // Versteckte Typen (hidden=true) werden nicht angezeigt
  async function fetchTypes() {
    const { data } = await adapterApi.list()
    return data.filter(a => !a.hidden).map(a => a.adapter_type)
  }

  // Legacy-Kompatibilität
  const adapters = instances

  return {
    adapters, instances, loading,
    fetchAdapters, createInstance, updateInstance, deleteInstance,
    testInstance, restartInstance, getSchema, fetchTypes,
  }
})
