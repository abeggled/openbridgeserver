<template>
  <div class="flex flex-col gap-5">
    <div>
      <h2 class="text-xl font-bold text-slate-100">Adapter</h2>
      <p class="text-sm text-slate-500 mt-0.5">Konfiguration und Status aller Protokoll-Adapter</p>
    </div>

    <div v-if="store.loading" class="flex justify-center py-20"><Spinner size="lg" /></div>

    <div v-else class="grid md:grid-cols-2 xl:grid-cols-2 gap-4">
      <div v-for="a in store.adapters" :key="a.adapter_type" class="card">
        <div class="card-header">
          <div class="flex items-center gap-3">
            <span :class="['w-3 h-3 rounded-full shrink-0', a.connected ? 'bg-green-400' : a.running ? 'bg-amber-400 animate-pulse' : 'bg-slate-600']" />
            <h3 class="font-semibold text-slate-100">{{ a.adapter_type }}</h3>
            <Badge :variant="a.connected ? 'success' : a.running ? 'warning' : 'muted'" size="xs">
              {{ a.connected ? 'Verbunden' : a.running ? 'Läuft' : 'Inaktiv' }}
            </Badge>
          </div>
          <button @click="toggleExpand(a.adapter_type)" class="btn-icon">
            <svg class="w-4 h-4 transition-transform" :class="expanded[a.adapter_type] ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
            </svg>
          </button>
        </div>

        <div class="px-5 py-3 flex gap-4 text-sm">
          <div class="text-slate-500">Bindings: <span class="text-slate-300 font-medium">{{ a.bindings }}</span></div>
        </div>

        <!-- Expanded config panel -->
        <div v-if="expanded[a.adapter_type]" class="border-t border-slate-700/60 p-5 flex flex-col gap-4">
          <div class="form-group">
            <label class="label">Konfiguration (JSON)</label>
            <textarea
              v-model="configDrafts[a.adapter_type]"
              class="input font-mono text-xs resize-none h-40"
              :placeholder="'{\n  \"host\": \"192.168.1.1\",\n  \"port\": 3671\n}'"
            />
          </div>

          <div v-if="testResults[a.adapter_type]" :class="[
            'flex items-center gap-2 p-3 rounded-lg text-sm',
            testResults[a.adapter_type].success ? 'bg-green-500/10 border border-green-500/30 text-green-400' : 'bg-red-500/10 border border-red-500/30 text-red-400'
          ]">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="testResults[a.adapter_type].success" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
            {{ testResults[a.adapter_type].detail }}
          </div>

          <div class="flex gap-3">
            <button @click="testConnection(a.adapter_type)" class="btn-secondary btn-sm" :disabled="testing[a.adapter_type]">
              <Spinner v-if="testing[a.adapter_type]" size="xs" color="slate" />
              Verbindung testen
            </button>
            <button @click="saveConfig(a.adapter_type)" class="btn-primary btn-sm" :disabled="saving[a.adapter_type]">
              <Spinner v-if="saving[a.adapter_type]" size="xs" color="white" />
              Speichern
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { adapterApi } from '@/api/client'
import { useAdapterStore } from '@/stores/adapters'
import Badge   from '@/components/ui/Badge.vue'
import Spinner from '@/components/ui/Spinner.vue'

const store       = useAdapterStore()
const expanded    = reactive({})
const configDrafts = reactive({})
const testResults  = reactive({})
const testing      = reactive({})
const saving       = reactive({})

onMounted(async () => {
  await store.fetchAdapters()
  // Load existing configs
  for (const a of store.adapters) {
    try {
      const { data } = await adapterApi.getConfig(a.adapter_type)
      configDrafts[a.adapter_type] = JSON.stringify(data.config, null, 2)
    } catch {
      configDrafts[a.adapter_type] = '{}'
    }
  }
})

function toggleExpand(type) {
  expanded[type] = !expanded[type]
}

async function testConnection(type) {
  testing[type] = true
  delete testResults[type]
  try {
    let cfg
    try { cfg = JSON.parse(configDrafts[type] || '{}') } catch { cfg = {} }
    const result = await store.testAdapter(type, cfg)
    testResults[type] = result
  } finally {
    testing[type] = false
  }
}

async function saveConfig(type) {
  saving[type] = true
  try {
    let cfg
    try { cfg = JSON.parse(configDrafts[type] || '{}') } catch {
      testResults[type] = { success: false, detail: 'Ungültiges JSON' }
      return
    }
    await store.saveConfig(type, cfg)
    testResults[type] = { success: true, detail: 'Konfiguration gespeichert' }
  } catch (e) {
    testResults[type] = { success: false, detail: e.response?.data?.detail ?? 'Fehler' }
  } finally {
    saving[type] = false
  }
}
</script>
