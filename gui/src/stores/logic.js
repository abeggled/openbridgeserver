import { defineStore } from 'pinia'
import { ref } from 'vue'
import { logicApi } from '@/api/client'

export const useLogicStore = defineStore('logic', () => {
  const graphs    = ref([])
  const nodeTypes = ref([])
  const loading   = ref(false)

  async function fetchNodeTypes() {
    const { data } = await logicApi.nodeTypes()
    nodeTypes.value = data
    return data
  }

  async function fetchGraphs() {
    loading.value = true
    try {
      const { data } = await logicApi.listGraphs()
      graphs.value = data
    } finally {
      loading.value = false
    }
  }

  async function createGraph(name, description = '') {
    const { data } = await logicApi.createGraph({
      name, description, enabled: true,
      flow_data: { nodes: [], edges: [] }
    })
    graphs.value.push(data)
    return data
  }

  async function saveGraph(id, name, description, enabled, flowData) {
    const { data } = await logicApi.saveGraph(id, {
      name, description, enabled,
      flow_data: flowData
    })
    const idx = graphs.value.findIndex(g => g.id === id)
    if (idx !== -1) graphs.value[idx] = data
    return data
  }

  async function deleteGraph(id) {
    await logicApi.deleteGraph(id)
    graphs.value = graphs.value.filter(g => g.id !== id)
  }

  async function runGraph(id) {
    const { data } = await logicApi.runGraph(id)
    return data
  }

  async function duplicateGraph(id) {
    const { data } = await logicApi.duplicateGraph(id)
    graphs.value.push(data)
    return data
  }

  async function importGraph(payload) {
    const { data } = await logicApi.importGraph(payload)
    graphs.value.push(data)
    return data
  }

  return { graphs, nodeTypes, loading, fetchNodeTypes, fetchGraphs, createGraph, saveGraph, deleteGraph, runGraph, duplicateGraph, importGraph }
})
