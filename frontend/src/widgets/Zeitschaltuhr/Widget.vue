<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useDatapointsStore } from '@/stores/datapoints'
import { getJwt, datapoints as dpApi } from '@/api/client'
import type { BindingOut } from '@/api/client'
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

const label          = computed(() => (props.config.label        as string | undefined) ?? '—')
const cfgDatapointId = computed(() => (props.config.datapoint_id as string | undefined) ?? '')
const cfgInstanceId  = computed(() => (props.config.instance_id  as string | undefined) ?? '')
const cfgMode        = computed((): 'full' | 'restricted' | 'minimal' => {
  const m = props.config.mode as string | undefined
  if (m === 'full' || m === 'restricted' || m === 'minimal') return m
  if (m === 'add_remove') return 'full'
  if (m === 'toggle')     return 'minimal'
  return 'full'
})

const hasConfig = computed(() => !!(cfgDatapointId.value && cfgInstanceId.value))

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

// ── ZSU bindings ──────────────────────────────────────────────────────────
const bindings  = ref<BindingOut[]>([])
const toggling  = ref(false)
const showModal = ref(false)

const canInteract = computed(() => !props.editorMode && hasConfig.value && !!getJwt())

const instanceBindings = computed(() =>
  bindings.value.filter((b) => b.adapter_instance_id === cfgInstanceId.value)
)

const anyEnabled = computed(() => instanceBindings.value.some((b) => b.enabled))

onMounted(async () => {
  if (!props.editorMode && cfgDatapointId.value) {
    try {
      bindings.value = await dpApi.listBindings(cfgDatapointId.value)
    } catch { /* ignore */ }
  }
})

async function handleCardClick() {
  if (!canInteract.value || toggling.value) return
  toggling.value = true
  try {
    const targetEnabled = !anyEnabled.value
    await Promise.all(
      instanceBindings.value.map((b) =>
        dpApi.updateBinding(cfgDatapointId.value, String(b.id), { enabled: targetEnabled })
      )
    )
    bindings.value = bindings.value.map((b) =>
      b.adapter_instance_id === cfgInstanceId.value ? { ...b, enabled: targetEnabled } : b
    )
  } catch { /* ignore */ } finally {
    toggling.value = false
  }
}

function openEdit(e: MouseEvent) {
  e.stopPropagation()
  if (!canInteract.value) return
  showModal.value = true
}
</script>

<template>
  <div
    class="flex flex-col h-full p-3 select-none gap-1.5"
    :class="canInteract ? 'cursor-pointer' : 'cursor-default'"
    @click="handleCardClick"
  >
    <!-- Kopfzeile -->
    <div class="flex items-center gap-1.5 min-w-0">
      <span class="text-sm leading-none flex-shrink-0">🕐</span>
      <span class="text-xs text-gray-500 dark:text-gray-400 truncate">{{ label }}</span>
      <template v-if="toggling">
        <span class="ml-auto text-xs text-gray-400 dark:text-gray-600">…</span>
      </template>
      <template v-else>
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
      </template>
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
    </div>

    <!-- Fusszeile: Zeitplan-Status + Edit-Button -->
    <div v-if="hasConfig" class="flex items-center gap-1.5 mt-auto">
      <span
        class="w-1.5 h-1.5 rounded-full flex-shrink-0"
        :class="instanceBindings.length === 0 || editorMode
          ? 'bg-gray-300 dark:bg-gray-600'
          : anyEnabled ? 'bg-blue-400' : 'bg-gray-400 dark:bg-gray-600'"
      />
      <span class="text-xs text-gray-400 dark:text-gray-500 truncate">
        <template v-if="editorMode">
          {{ cfgMode === 'full' ? 'Vollzugriff' : cfgMode === 'restricted' ? 'Eingeschränkt' : 'Minimal' }}
        </template>
        <template v-else-if="instanceBindings.length === 0">Keine Schaltpunkte</template>
        <template v-else>{{ anyEnabled ? 'Zeitplan aktiv' : 'Zeitplan inaktiv' }}</template>
      </span>
      <button
        v-if="canInteract"
        type="button"
        title="Schaltpunkte bearbeiten"
        class="ml-auto text-xs text-gray-400 dark:text-gray-600 hover:text-blue-500 dark:hover:text-blue-400 px-1 rounded transition-colors leading-none"
        @click="openEdit"
      >✏️</button>
    </div>
  </div>

  <!-- Modal -->
  <Teleport to="body">
    <ZeitschaltuhrAddRemoveModal
      v-if="showModal"
      :datapoint-id="cfgDatapointId"
      :instance-id="cfgInstanceId"
      :mode="cfgMode"
      @close="showModal = false"
    />
  </Teleport>
</template>
