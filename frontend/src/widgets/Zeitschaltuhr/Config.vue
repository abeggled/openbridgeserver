<script setup lang="ts">
import { reactive, ref, watch, onMounted, computed } from 'vue'
import { adapters as adaptersApi, type InstanceBindingEntry } from '@/api/client'

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

const instances = ref<ZSUInstance[]>([])
const bindings = ref<InstanceBindingEntry[]>([])
const loadingInstances = ref(false)
const loadingBindings = ref(false)
const errorMsg = ref('')

const cfg = reactive({
  label:           (props.modelValue.label           as string)  ?? '',
  instance_id:     (props.modelValue.instance_id     as string)  ?? '',
  datapoint_id:    (props.modelValue.datapoint_id    as string)  ?? '',
  binding_id:      (props.modelValue.binding_id      as string)  ?? '',
  binding_enabled: (props.modelValue.binding_enabled as boolean) ?? true,
})

/** Einmalig pro Datenpunkt-ID (für Dropdown 2) */
const availableDatapoints = computed(() => {
  const seen = new Set<string>()
  return bindings.value.filter((b) => {
    if (seen.has(b.datapoint_id)) return false
    seen.add(b.datapoint_id)
    return true
  })
})

/** Alle Verknüpfungen des gewählten Datenpunkts (für Dropdown 3) */
const availableBindings = computed(() =>
  bindings.value.filter((b) => b.datapoint_id === cfg.datapoint_id)
)

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

  if (cfg.instance_id) {
    await loadBindings(cfg.instance_id)
  }
})

async function loadBindings(instanceId: string) {
  loadingBindings.value = true
  errorMsg.value = ''
  try {
    bindings.value = await adaptersApi.instanceBindings(instanceId)
  } catch {
    errorMsg.value = 'Verknüpfungen konnten nicht geladen werden.'
    bindings.value = []
  } finally {
    loadingBindings.value = false
  }
}

async function onInstanceChange() {
  cfg.datapoint_id = ''
  cfg.binding_id = ''
  cfg.binding_enabled = true
  bindings.value = []
  if (cfg.instance_id) await loadBindings(cfg.instance_id)
}

function onDatapointChange() {
  cfg.binding_id = ''
  cfg.binding_enabled = true
  // Automatisch wählen wenn nur eine Verknüpfung vorhanden
  const matches = availableBindings.value
  if (matches.length === 1) {
    cfg.binding_id = matches[0].binding_id
    cfg.binding_enabled = matches[0].enabled
  }
}

function onBindingChange() {
  const b = bindings.value.find((b) => b.binding_id === cfg.binding_id)
  if (b) cfg.binding_enabled = b.enabled
}

/** Lesbare Bezeichnung für eine Verknüpfung */
function bindingLabel(b: InstanceBindingEntry): string {
  const c = b.config
  const type  = (c.timer_type as string | undefined) ?? 'daily'
  const ref   = (c.time_ref   as string | undefined) ?? 'absolute'
  const val   = (c.value      as string | undefined) ?? '?'

  let timeStr = ''
  if (ref === 'absolute') {
    const h = String((c.hour   as number | undefined) ?? 0).padStart(2, '0')
    const m = String((c.minute as number | undefined) ?? 0).padStart(2, '0')
    timeStr = `${h}:${m}`
  } else if (ref === 'sunrise')        timeStr = 'Sonnenaufgang'
  else if (ref === 'sunset')           timeStr = 'Sonnenuntergang'
  else if (ref === 'solar_noon')       timeStr = 'Sonnenmittag'
  else if (ref === 'solar_altitude')   timeStr = `Sonne ${c.solar_altitude_deg ?? '?'}°`

  const typeStr = type === 'meta' ? 'Meta' : type === 'annual' ? 'jährlich' : 'täglich'
  const suffix  = b.enabled ? '' : ' ⚠ deaktiviert'

  return `${typeStr} ${timeStr} → ${val}${suffix}`.trim()
}

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-3">

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Beschriftung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Licht EG Nacht"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Dropdown 1: Zeitschaltuhr-Instanz -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Zeitschaltuhr</label>
      <select
        v-model="cfg.instance_id"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
        @change="onInstanceChange"
      >
        <option value="">
          {{ loadingInstances ? 'Lade …' : instances.length === 0 ? 'Keine Zeitschaltuhr konfiguriert' : '— Zeitschaltuhr wählen —' }}
        </option>
        <option v-for="inst in instances" :key="inst.id" :value="inst.id">
          {{ inst.name }}
        </option>
      </select>
    </div>

    <!-- Dropdown 2: Objekt (Datenpunkt) -->
    <div v-if="cfg.instance_id">
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Objekt</label>
      <select
        v-model="cfg.datapoint_id"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 disabled:opacity-50"
        :disabled="loadingBindings"
        @change="onDatapointChange"
      >
        <option value="">
          {{ loadingBindings ? 'Lade …' : availableDatapoints.length === 0 ? 'Keine Objekte gefunden' : '— Objekt wählen —' }}
        </option>
        <option v-for="dp in availableDatapoints" :key="dp.datapoint_id" :value="dp.datapoint_id">
          {{ dp.datapoint_name }}
        </option>
      </select>
    </div>

    <!-- Dropdown 3: Verknüpfung -->
    <div v-if="cfg.datapoint_id">
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Verknüpfung</label>
      <select
        v-model="cfg.binding_id"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
        @change="onBindingChange"
      >
        <option value="">— Verknüpfung wählen —</option>
        <option v-for="b in availableBindings" :key="b.binding_id" :value="b.binding_id">
          {{ bindingLabel(b) }}
        </option>
      </select>
    </div>

    <!-- Fehlermeldung -->
    <p v-if="errorMsg" class="text-xs text-red-400">{{ errorMsg }}</p>

  </div>
</template>
