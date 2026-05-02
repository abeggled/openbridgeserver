<script setup lang="ts">
import { reactive, ref, watch, onMounted } from 'vue'
import { adapters as adaptersApi, datapoints as datapointsApi } from '@/api/client'

const props = defineProps<{
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, unknown>): void
}>()

interface ZSUInstance {
  id: string
  name: string
}

interface DpOption {
  id: string
  name: string
}

const instances = ref<ZSUInstance[]>([])
const loadingInstances = ref(false)
const errorMsg = ref('')

const dpQuery = ref('')
const dpResults = ref<DpOption[]>([])
const dpSearchLoading = ref(false)
const dpSearchOpen = ref(false)

const cfg = reactive({
  label:        (props.modelValue.label        as string) ?? '',
  instance_id:  (props.modelValue.instance_id  as string) ?? '',
  datapoint_id: (props.modelValue.datapoint_id as string) ?? '',
  mode:         (props.modelValue.mode         as string) ?? 'full',
})

// Normalize legacy mode values
if (cfg.mode === 'add_remove') cfg.mode = 'full'
if (cfg.mode === 'toggle')     cfg.mode = 'minimal'

onMounted(async () => {
  loadingInstances.value = true
  errorMsg.value = ''
  try {
    const all = await adaptersApi.listInstances()
    instances.value = all
      .filter((i) => i.adapter_type.toLowerCase() === 'zeitschaltuhr')
      .map((i) => ({ id: i.id, name: i.name }))
  } catch {
    errorMsg.value = 'Zeitschaltuhr-Instanzen konnten nicht geladen werden.'
  } finally {
    loadingInstances.value = false
  }

  if (cfg.datapoint_id) {
    try {
      const dp = await datapointsApi.get(cfg.datapoint_id)
      dpQuery.value = dp.name
    } catch { /* ignore */ }
  }
})

let _dpSearchTimer: ReturnType<typeof setTimeout> | null = null

async function onDpQueryInput() {
  cfg.datapoint_id = ''
  if (_dpSearchTimer) clearTimeout(_dpSearchTimer)
  if (!dpQuery.value.trim()) {
    dpResults.value = []
    dpSearchOpen.value = false
    return
  }
  _dpSearchTimer = setTimeout(async () => {
    dpSearchLoading.value = true
    try {
      const res = await datapointsApi.search(dpQuery.value.trim(), 0, 20)
      dpResults.value = res.items.map((dp) => ({ id: dp.id, name: dp.name }))
      dpSearchOpen.value = dpResults.value.length > 0
    } catch {
      dpResults.value = []
    } finally {
      dpSearchLoading.value = false
    }
  }, 250)
}

function selectDp(dp: DpOption) {
  cfg.datapoint_id = dp.id
  dpQuery.value = dp.name
  dpResults.value = []
  dpSearchOpen.value = false
}

function clearDp() {
  cfg.datapoint_id = ''
  dpQuery.value = ''
  dpResults.value = []
  dpSearchOpen.value = false
}

const selCls = 'w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 disabled:opacity-50'
const lCls   = 'block text-xs text-gray-500 dark:text-gray-400 mb-1'

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-3">

    <!-- Beschriftung -->
    <div>
      <label :class="lCls">Beschriftung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Licht EG Nacht"
        :class="selCls"
      />
    </div>

    <!-- Widget-Modus als 3 Buttons -->
    <div>
      <label :class="lCls">Widget-Modus</label>
      <div class="flex gap-1">
        <button
          v-for="m in [
            { value: 'full',       label: 'Vollzugriff',   title: 'Hinzufügen, bearbeiten, löschen' },
            { value: 'restricted', label: 'Eingeschränkt', title: 'Nur bearbeiten und aktivieren' },
            { value: 'minimal',    label: 'Minimal',       title: 'Nur aktivieren/deaktivieren' },
          ]"
          :key="m.value"
          type="button"
          :title="m.title"
          class="flex-1 px-2 py-1.5 text-xs rounded border transition-colors"
          :class="cfg.mode === m.value
            ? 'bg-blue-600 border-blue-600 text-white'
            : 'bg-gray-50 dark:bg-gray-800 border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:border-blue-400 dark:hover:border-blue-500'"
          @click="cfg.mode = m.value"
        >{{ m.label }}</button>
      </div>
    </div>

    <!-- Zeitschaltuhr-Instanz -->
    <div>
      <label :class="lCls">Zeitschaltuhr</label>
      <select v-model="cfg.instance_id" :class="selCls">
        <option value="">
          {{ loadingInstances ? 'Lade …' : instances.length === 0 ? 'Keine Zeitschaltuhr konfiguriert' : '— Zeitschaltuhr wählen —' }}
        </option>
        <option v-for="inst in instances" :key="inst.id" :value="inst.id">
          {{ inst.name }}
        </option>
      </select>
    </div>

    <!-- Datenpunkt-Suche (für alle Modi gleich) -->
    <template v-if="cfg.instance_id">
      <div class="relative">
        <label :class="lCls">
          Objekt
          <span class="text-gray-400 dark:text-gray-600">(Datenpunkt suchen)</span>
        </label>
        <div class="flex gap-1">
          <input
            v-model="dpQuery"
            type="text"
            placeholder="Name eingeben …"
            :class="[selCls, 'flex-1', cfg.datapoint_id ? 'border-blue-500' : '']"
            @input="onDpQueryInput"
            @focus="dpSearchOpen = dpResults.length > 0"
          />
          <button
            v-if="cfg.datapoint_id"
            type="button"
            class="px-2 text-gray-400 hover:text-red-400 text-sm"
            title="Auswahl aufheben"
            @click="clearDp"
          >×</button>
        </div>
        <!-- Suchergebnisse -->
        <div
          v-if="dpSearchOpen && dpResults.length"
          class="absolute z-10 mt-0.5 w-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded shadow-lg max-h-48 overflow-y-auto"
        >
          <button
            v-for="dp in dpResults"
            :key="dp.id"
            type="button"
            class="w-full text-left px-3 py-1.5 text-sm hover:bg-blue-50 dark:hover:bg-blue-900/30 text-gray-800 dark:text-gray-200 truncate"
            @click="selectDp(dp)"
          >{{ dp.name }}</button>
        </div>
        <p v-if="dpSearchLoading" class="text-xs text-gray-400 mt-0.5">Suche …</p>
        <p v-if="cfg.datapoint_id" class="text-xs text-blue-500 dark:text-blue-400 mt-0.5">
          ✓ Ausgewählt
        </p>
      </div>
    </template>

    <!-- Fehlermeldung -->
    <p v-if="errorMsg" class="text-xs text-red-400">{{ errorMsg }}</p>

  </div>
</template>
