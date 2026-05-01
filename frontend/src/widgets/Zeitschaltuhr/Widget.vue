<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDatapointsStore } from '@/stores/datapoints'
import { getJwt, datapoints as dpApi } from '@/api/client'
import ZeitschaltuhrBindingModal from '@/components/ZeitschaltuhrBindingModal.vue'
import ZeitschaltuhrAddRemoveModal from '@/components/ZeitschaltuhrAddRemoveModal.vue'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()

const dpStore = useDatapointsStore()

const label          = computed(() => (props.config.label        as string  | undefined) ?? '—')
const cfgDatapointId = computed(() => (props.config.datapoint_id as string  | undefined) ?? '')
const cfgBindingId   = computed(() => (props.config.binding_id   as string  | undefined) ?? '')
const cfgInstanceId  = computed(() => (props.config.instance_id  as string  | undefined) ?? '')
const cfgMode        = computed(() => (props.config.mode         as string  | undefined) ?? 'full')
const hasBinding     = computed(() => !!(cfgDatapointId.value && cfgBindingId.value))

/** Live-Wert aus dem Datenpunkt-Store (wird via getExtraDatapointIds abonniert) */
const liveValue = computed((): DataPointValue | null => {
  if (props.editorMode || !cfgDatapointId.value) return null
  return dpStore.getValue(cfgDatapointId.value)
})

function resolveBool(v: DataPointValue | null): boolean | null {
  if (v === null) return null
  const raw = v.v
  if (typeof raw === 'boolean') return raw
  if (typeof raw === 'number')  return raw !== 0
  if (typeof raw === 'string')  return raw === '1' || raw.toLowerCase() === 'true' || raw.toLowerCase() === 'on'
  return null
}

const outputActive = computed(() => resolveBool(liveValue.value))
const quality      = computed(() => liveValue.value?.q ?? null)

/** Binding-Status: aus Config (Snapshot, aktualisiert nach Modal-Speichern) */
const bindingEnabled = ref((props.config.binding_enabled as boolean | undefined) ?? true)

/** Modals */
const canEdit      = computed(() => !props.editorMode && hasBinding.value && !!getJwt())
const canAddRemove = computed(() => !props.editorMode && !!cfgDatapointId.value && !!cfgInstanceId.value && !!getJwt())
const showFullModal      = ref(false)
const showAddRemoveModal = ref(false)
const toggling           = ref(false)

async function handleClick() {
  const mode = cfgMode.value
  if (mode === 'toggle') {
    if (!canEdit.value || toggling.value) return
    toggling.value = true
    try {
      const newEnabled = !bindingEnabled.value
      await dpApi.updateBinding(cfgDatapointId.value, cfgBindingId.value, { enabled: newEnabled })
      bindingEnabled.value = newEnabled
    } catch {
      // ignore — no toast available in widget context
    } finally {
      toggling.value = false
    }
  } else if (mode === 'add_remove') {
    if (!canAddRemove.value) return
    showAddRemoveModal.value = true
  } else {
    // full (default)
    if (!canEdit.value) return
    showFullModal.value = true
  }
}

function onFullSaved(enabled: boolean) {
  bindingEnabled.value = enabled
  showFullModal.value = false
}

function onAddRemoveClosed() {
  showAddRemoveModal.value = false
}
</script>

<template>
  <div
    class="flex flex-col h-full p-3 select-none gap-1.5"
    :class="(canEdit || canAddRemove) ? 'cursor-pointer' : 'cursor-default'"
    @click="handleClick"
  >
    <!-- Kopfzeile: Icon + Beschriftung + Qualitätsindikator -->
    <div class="flex items-center gap-1.5 min-w-0">
      <span class="text-sm leading-none flex-shrink-0">🕐</span>
      <span class="text-xs text-gray-500 dark:text-gray-400 truncate">{{ label }}</span>
      <span
        v-if="quality === 'bad'"
        class="ml-auto w-2 h-2 rounded-full bg-red-500 flex-shrink-0"
        title="Qualität: schlecht"
      />
      <span
        v-else-if="quality === 'uncertain'"
        class="ml-auto w-2 h-2 rounded-full bg-yellow-400 flex-shrink-0"
        title="Qualität: undefiniert"
      />
    </div>

    <!-- Ausgang: AKTIV / INAKTIV -->
    <div class="flex items-center gap-2 mt-1">
      <span
        class="inline-flex items-center px-2.5 py-1 rounded-md text-sm font-semibold leading-none"
        :class="outputActive === null
          ? 'bg-gray-200 dark:bg-gray-700 text-gray-400 dark:text-gray-500'
          : outputActive
            ? 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-400'
            : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'"
      >
        {{ outputActive === null ? (editorMode ? '—' : '…') : outputActive ? 'AKTIV' : 'INAKTIV' }}
      </span>
      <!-- Toggle-Spinner -->
      <span v-if="toggling" class="text-xs text-gray-400 dark:text-gray-500 animate-pulse">…</span>
    </div>

    <!-- Aktivierungsstatus der Verknüpfung -->
    <div v-if="hasBinding" class="flex items-center gap-1.5 mt-auto">
      <span
        class="w-1.5 h-1.5 rounded-full flex-shrink-0"
        :class="bindingEnabled ? 'bg-blue-400' : 'bg-gray-400 dark:bg-gray-600'"
      />
      <span class="text-xs text-gray-400 dark:text-gray-500">
        {{ bindingEnabled ? 'ZSU aktiviert' : 'ZSU deaktiviert' }}
      </span>
      <!-- Modus-Hinweis für Admins -->
      <span v-if="canEdit && cfgMode === 'full'" class="ml-auto text-xs text-gray-600 dark:text-gray-700">✏️</span>
      <span v-else-if="canEdit && cfgMode === 'toggle'" class="ml-auto text-xs text-gray-600 dark:text-gray-700">⏯</span>
      <span v-else-if="canAddRemove && cfgMode === 'add_remove'" class="ml-auto text-xs text-gray-600 dark:text-gray-700">±</span>
    </div>
  </div>

  <!-- Modals (Teleport ins body, damit sie über allem liegen) -->
  <Teleport to="body">
    <ZeitschaltuhrBindingModal
      v-if="showFullModal"
      :datapoint-id="cfgDatapointId"
      :binding-id="cfgBindingId"
      @close="showFullModal = false"
      @saved="onFullSaved"
    />
    <ZeitschaltuhrAddRemoveModal
      v-if="showAddRemoveModal"
      :datapoint-id="cfgDatapointId"
      :instance-id="cfgInstanceId"
      @close="onAddRemoveClosed"
    />
  </Teleport>
</template>
