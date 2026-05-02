<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
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
const bindings        = ref<BindingOut[]>([])
const toggling        = ref(false)
const showModal       = ref(false)
const confirmPending  = ref(false)
const pendingTarget   = ref(false)   // true = enable all, false = disable all

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

function handleCardClick() {
  if (!canInteract.value || toggling.value || confirmPending.value) return
  pendingTarget.value = !anyEnabled.value
  confirmPending.value = true
}

async function executeToggle() {
  confirmPending.value = false
  toggling.value = true
  try {
    const targetEnabled = pendingTarget.value
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

function cancelToggle() {
  confirmPending.value = false
}

function openEdit(e: MouseEvent) {
  e.stopPropagation()
  if (!canInteract.value) return
  showModal.value = true
}

watch(showModal, async (open) => {
  if (!open && cfgDatapointId.value) {
    try {
      bindings.value = await dpApi.listBindings(cfgDatapointId.value)
    } catch { /* ignore */ }
  }
})
</script>

<template>
  <div class="relative h-full">

    <!-- Haupt-Inhalt -->
    <div
      class="flex flex-col h-full p-3 select-none"
      :class="canInteract && !confirmPending ? 'cursor-pointer' : 'cursor-default'"
      @click="handleCardClick"
    >
      <!-- Kopfzeile -->
      <div class="flex items-center gap-1.5 min-w-0">
        <span class="text-sm leading-none flex-shrink-0">🕐</span>
        <span class="text-xs text-gray-500 dark:text-gray-400 truncate" data-testid="zsu-label">{{ label }}</span>
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

      <!-- Ausgang: effektiver Objektwert -->
      <div class="flex items-center gap-2 mt-3">
        <span
          class="inline-flex items-center px-2.5 py-1 rounded-md font-semibold leading-none text-base"
          :class="outputActive === null
            ? 'bg-gray-200 dark:bg-gray-700 text-gray-400 dark:text-gray-500'
            : outputActive
              ? 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-400'
              : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'"
        >
          {{ liveValue !== null ? String(liveValue.v) : (editorMode ? '—' : '…') }}
        </span>
      </div>

      <!-- Zeitplan-Status -->
      <div v-if="hasConfig" class="flex items-start gap-2 mt-3 min-w-0">
        <span
          class="w-3 h-3 rounded-full flex-shrink-0 mt-0.5"
          :class="instanceBindings.length === 0 || editorMode
            ? 'bg-gray-300 dark:bg-gray-600'
            : anyEnabled ? 'bg-green-500' : 'bg-red-500'"
        />
        <span
          class="text-xs font-medium leading-snug"
          :class="instanceBindings.length === 0 || editorMode
            ? 'text-gray-400 dark:text-gray-500'
            : anyEnabled ? 'text-green-600 dark:text-green-400' : 'text-red-500 dark:text-red-400'"
        >
          <template v-if="editorMode">
            {{ cfgMode === 'full' ? 'Vollzugriff' : cfgMode === 'restricted' ? 'Eingeschränkt' : 'Minimal' }}
          </template>
          <template v-else-if="instanceBindings.length === 0"><span data-testid="zsu-status">Keine Schaltpunkte</span></template>
          <template v-else><span data-testid="zsu-status">{{ anyEnabled ? 'Zeitschaltuhr aktiv' : 'Zeitschaltuhr inaktiv' }}</span></template>
        </span>
      </div>

      <!-- Spacer -->
      <div class="flex-1" />

      <!-- Edit-Button am unteren Rand -->
      <div v-if="canInteract" class="flex justify-end mt-1">
        <button
          type="button"
          title="Schaltpunkte bearbeiten"
          data-testid="zsu-edit-btn"
          class="text-xs text-gray-400 dark:text-gray-600 hover:text-blue-500 dark:hover:text-blue-400 px-1 rounded transition-colors leading-none"
          @click="openEdit"
        >✏️</button>
      </div>
    </div>

    <!-- Bestätigungs-Overlay -->
    <Transition
      enter-active-class="transition-opacity duration-150"
      leave-active-class="transition-opacity duration-100"
      enter-from-class="opacity-0"
      leave-to-class="opacity-0"
    >
      <div
        v-if="confirmPending"
        data-testid="zsu-confirm-overlay"
        class="absolute inset-0 flex flex-col items-center justify-center gap-2 p-3 rounded bg-white/95 dark:bg-gray-900/95 z-10"
        @click.stop
      >
        <p class="text-xs text-center text-gray-700 dark:text-gray-200 leading-tight font-medium">
          Zeitschaltuhr {{ pendingTarget ? 'aktivieren' : 'deaktivieren' }}?
        </p>
        <div class="flex gap-2">
          <button
            type="button"
            data-testid="zsu-confirm-no"
            class="px-3 py-1 rounded text-xs bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
            @click.stop="cancelToggle"
          >Nein</button>
          <button
            type="button"
            data-testid="zsu-confirm-yes"
            class="px-3 py-1 rounded text-xs text-white transition-colors"
            :class="pendingTarget ? 'bg-green-600 hover:bg-green-500' : 'bg-red-600 hover:bg-red-500'"
            @click.stop="executeToggle"
          >Ja</button>
        </div>
      </div>
    </Transition>

  </div>

  <!-- Schaltpunkte-Modal -->
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
