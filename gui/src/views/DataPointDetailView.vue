<template>
  <div class="flex flex-col gap-5" v-if="dp">
    <!-- Breadcrumb + header -->
    <div>
      <RouterLink to="/datapoints" class="text-sm text-blue-400 hover:underline">← Objekte</RouterLink>
      <div class="flex flex-wrap items-start gap-3 mt-2">
        <div class="flex-1">
          <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ dp.name }}</h2>
          <p class="text-sm text-slate-500 font-mono mt-0.5">{{ dp.id }}</p>
        </div>
        <Badge variant="info">{{ dp.data_type }}</Badge>
        <Badge :variant="qualityVariant(liveState?.quality ?? dp.quality)" dot>
          {{ qualityLabel(liveState?.quality ?? dp.quality) ?? '—' }}
        </Badge>
      </div>
    </div>

    <div class="grid lg:grid-cols-3 gap-4">
      <!-- Current value card -->
      <div class="card p-5 flex flex-col gap-3">
        <div class="text-xs font-semibold text-slate-500 uppercase tracking-wide">Aktueller Wert</div>
        <div class="text-4xl font-bold font-mono text-blue-600 dark:text-blue-300">
          {{ displayVal }}
        </div>
        <div class="text-xs text-slate-500">
          {{ liveState?.ts ? new Date(liveState.ts).toLocaleString('de-CH') : dp.updated_at }}
        </div>
        <div class="font-mono text-xs text-slate-600 break-all">{{ dp.mqtt_topic }}</div>
        <div v-if="dp.mqtt_alias" class="font-mono text-xs text-slate-600 break-all">{{ dp.mqtt_alias }}</div>
        <div class="border-t border-slate-200 dark:border-slate-700 pt-4 mt-1">
          <div class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Wert schreiben</div>
          <div v-if="hasWritableBinding" class="flex flex-wrap items-center gap-2">
            <template v-if="dp.data_type === 'BOOLEAN'">
              <button
                @click="writeDetailValue(true)"
                :disabled="writeBusy"
                class="btn-secondary btn-sm"
                :class="{ 'ring-2 ring-blue-400': currentRawValue === true }"
              >
                true
              </button>
              <button
                @click="writeDetailValue(false)"
                :disabled="writeBusy"
                class="btn-secondary btn-sm"
                :class="{ 'ring-2 ring-blue-400': currentRawValue === false }"
              >
                false
              </button>
            </template>
            <template v-else>
              <input
                v-model="writeDraft"
                type="text"
                class="input flex-1 min-w-36"
                @keyup.enter="writeDetailValue(writeDraft)"
              />
              <button @click="writeDetailValue(writeDraft)" :disabled="writeBusy" class="btn-primary btn-sm">
                Schreiben
              </button>
            </template>
          </div>
          <div v-else class="text-xs text-slate-500">
            Kein schreibbares Binding vorhanden.
          </div>
          <div v-if="writeFeedback" class="text-xs mt-2" :class="writeFeedback.type === 'error' ? 'text-red-500' : 'text-green-600'">
            {{ writeFeedback.text }}
          </div>
        </div>
      </div>

      <!-- Properties -->
      <div class="card p-5 col-span-2">
        <div class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-4">Eigenschaften</div>
        <dl class="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
          <dt class="text-slate-500">Name</dt>       <dd class="text-slate-700 dark:text-slate-200">{{ dp.name }}</dd>
          <dt class="text-slate-500">Datentyp</dt>   <dd><Badge variant="info" size="xs">{{ dp.data_type }}</Badge></dd>
          <dt class="text-slate-500">Einheit</dt>    <dd class="text-slate-700 dark:text-slate-200">{{ dp.unit ?? '—' }}</dd>
          <dt class="text-slate-500">Tags</dt>
          <dd class="flex flex-wrap gap-1">
            <Badge v-for="t in dp.tags" :key="t" variant="default" size="xs">{{ t }}</Badge>
            <span v-if="!dp.tags?.length" class="text-slate-500">—</span>
          </dd>
          <dt class="text-slate-500">Wert speichern</dt>
          <dd>
            <Badge :variant="dp.persist_value ? 'success' : 'muted'" size="xs">{{ dp.persist_value ? 'Ja' : 'Nein' }}</Badge>
          </dd>
          <dt class="text-slate-500">Historisierung</dt>
          <dd>
            <Badge :variant="dp.record_history ? 'success' : 'muted'" size="xs" data-testid="badge-record-history">{{ dp.record_history ? 'Aktiv' : 'Deaktiviert' }}</Badge>
          </dd>
          <dt class="text-slate-500">Erstellt</dt>   <dd class="text-slate-400 text-xs">{{ new Date(dp.created_at).toLocaleString('de-CH') }}</dd>
          <dt class="text-slate-500">Geändert</dt>   <dd class="text-slate-400 text-xs">{{ new Date(dp.updated_at).toLocaleString('de-CH') }}</dd>
        </dl>
        <div class="flex gap-3 mt-5">
          <button @click="showEdit = true" class="btn-secondary btn-sm">Bearbeiten</button>
          <RouterLink :to="`/history?dp=${dp.id}`" class="btn-secondary btn-sm">Historie →</RouterLink>
        </div>
      </div>
    </div>

    <!-- Hierarchie-Zuordnungen -->
    <DataPointHierarchyCard :dp-id="id" />

    <!-- Verknüpfungen -->
    <div class="card">
      <div class="card-header">
        <h3 class="font-semibold text-slate-800 dark:text-slate-100 text-sm">Adapter Verknüpfungen</h3>
        <button @click="showBindingForm = true" class="btn-primary btn-sm" data-testid="btn-add-binding">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          Verknüpfung
        </button>
      </div>
      <div class="card-body">
        <div v-if="bindingsLoading" class="flex justify-center py-4"><Spinner /></div>
        <div v-else-if="!bindings.length" class="text-center text-slate-500 text-sm py-4">Keine Verknüpfungen — Objekt ist noch mit keinem Adapter verknüpft</div>
        <div v-else class="flex flex-col gap-2">
          <div v-for="b in bindings" :key="b.id" class="flex items-center gap-3 p-3 bg-surface-700 rounded-lg">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ b.adapter_type }}</span>
                <Badge :variant="b.direction === 'SOURCE' ? 'info' : b.direction === 'DEST' ? 'warning' : 'success'" size="xs">
                  {{ b.direction === 'SOURCE' ? 'Lesen' : b.direction === 'DEST' ? 'Schreiben' : 'Lesen/Schreiben' }}
                </Badge>
                <Badge v-if="!b.enabled" variant="danger" size="xs">Deaktiviert</Badge>
              </div>
              <div class="text-xs text-slate-500 font-mono mt-1 truncate">{{ JSON.stringify(b.config) }}</div>
            </div>
            <div class="flex gap-1 shrink-0">
              <button @click="openEditBinding(b)" class="btn-icon" title="Bearbeiten">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
              </button>
              <button @click="confirmDeleteBinding(b)" class="btn-icon text-red-400" title="Löschen">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Logic Verknüpfungen -->
    <div class="card">
      <div class="card-header">
        <h3 class="font-semibold text-slate-800 dark:text-slate-100 text-sm">Logik Verknüpfungen</h3>
      </div>
      <div class="card-body">
        <div v-if="logicUsagesLoading" class="flex justify-center py-4"><Spinner /></div>
        <div v-else-if="!logicUsages.length" class="text-center text-slate-500 text-sm py-4">
          Objekt wird in keinem Logic-Sheet verwendet
        </div>
        <div v-else class="flex flex-col gap-2">
          <div v-for="u in logicUsages" :key="u.node_id" class="flex items-center gap-3 p-3 bg-surface-700 rounded-lg">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ u.graph_name }}</span>
                <Badge :variant="u.direction === 'SOURCE' ? 'info' : 'warning'" size="xs">
                  {{ u.direction === 'SOURCE' ? 'Lesen' : 'Schreiben' }}
                </Badge>
                <Badge v-if="!u.graph_enabled" variant="muted" size="xs">Deaktiviert</Badge>
              </div>
              <div class="text-xs text-slate-500 mt-1">
                {{ u.node_type === 'datapoint_read' ? 'Logik liest im verlinkten Blatt dieses Objekt' : 'Logik schreibt im verlinkten Blatt auf dieses Objekt' }}
              </div>
            </div>
            <RouterLink :to="`/logic?graph=${u.graph_id}`" class="btn-icon shrink-0" title="Logic-Sheet öffnen">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
            </RouterLink>
          </div>
        </div>
      </div>
    </div>

    <!-- Edit Objekt Modal -->
    <Modal v-model="showEdit" title="Objekt bearbeiten">
      <DataPointForm :initial="dp" :datatypes="dpStore.datatypes" :save-handler="onEditSave" @cancel="showEdit = false" />
    </Modal>

    <!-- Binding form Modal -->
    <Modal v-model="showBindingForm" :title="editBinding ? 'Binding bearbeiten' : 'Neues Binding'" max-width="xl">
      <BindingForm :dp-id="id" :initial="editBinding" :dp-persist-value="dp?.persist_value ?? false" :dp-data-type="dp?.data_type ?? 'UNKNOWN'" @save="onBindingSave" @cancel="showBindingForm = false" />
    </Modal>

    <!-- Delete binding confirm -->
    <ConfirmDialog v-model="showBindingConfirm" title="Binding löschen"
      message="Dieses Binding wirklich löschen?"
      confirm-label="Löschen" @confirm="doDeleteBinding" />
  </div>

  <div v-else class="flex justify-center py-20"><Spinner size="lg" /></div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { dpApi, logicApi } from '@/api/client'
import { useDatapointStore } from '@/stores/datapoints'
import { useWebSocketStore } from '@/stores/websocket'
import Badge          from '@/components/ui/Badge.vue'
import Spinner        from '@/components/ui/Spinner.vue'
import Modal          from '@/components/ui/Modal.vue'
import ConfirmDialog  from '@/components/ui/ConfirmDialog.vue'
import DataPointForm          from '@/components/datapoints/DataPointForm.vue'
import BindingForm            from '@/components/datapoints/BindingForm.vue'
import DataPointHierarchyCard from '@/components/datapoints/DataPointHierarchyCard.vue'

const props   = defineProps({ id: { type: String, required: true } })
const dpStore = useDatapointStore()
const ws      = useWebSocketStore()

const dp                  = ref(null)
const bindings            = ref([])
const bindingsLoading     = ref(false)
const logicUsages         = ref([])
const logicUsagesLoading  = ref(false)
const showEdit            = ref(false)
const showBindingForm = ref(false)
const showBindingConfirm = ref(false)
const editBinding     = ref(null)
const deleteBindingTarget = ref(null)
const writeDraft      = ref('')
const writeBusy       = ref(false)
const writeFeedback   = ref(null)
let unsubWs = null

const liveState  = computed(() => ws.liveValues[props.id])
const currentRawValue = computed(() => liveState.value?.value ?? dp.value?.value)
const displayVal = computed(() => {
  const v = currentRawValue.value
  if (v === null || v === undefined) return '—'
  return dp.value?.unit ? `${v} ${dp.value.unit}` : String(v)
})
const hasWritableBinding = computed(() =>
  bindings.value.some(b => b.enabled && ['DEST', 'BOTH'].includes(b.direction))
)

watch(currentRawValue, (value) => {
  if (!writeBusy.value && value !== undefined && value !== null) writeDraft.value = String(value)
})

onMounted(async () => {
  await dpStore.loadDatatypes()
  const { data } = await dpApi.get(props.id)
  dp.value = data
  if (data.value !== undefined && data.value !== null) writeDraft.value = String(data.value)
  ws.subscribe([props.id])
  unsubWs = ws.onValue((id, value, quality) => {
    if (id === props.id && dp.value) { dp.value.value = value; dp.value.quality = quality }
  })
  await Promise.all([loadBindings(), loadLogicUsages()])
})
onUnmounted(() => unsubWs?.())

async function loadBindings() {
  bindingsLoading.value = true
  try { const { data } = await dpApi.listBindings(props.id); bindings.value = data }
  finally { bindingsLoading.value = false }
}

async function loadLogicUsages() {
  logicUsagesLoading.value = true
  try { const { data } = await logicApi.datapointUsages(props.id); logicUsages.value = data }
  finally { logicUsagesLoading.value = false }
}

async function onEditSave(payload) {
  const updated = await dpStore.update(props.id, payload)
  dp.value = updated
  showEdit.value = false   // only reached if no error thrown
}

function openEditBinding(b) { editBinding.value = b; showBindingForm.value = true }
async function onBindingSave() { showBindingForm.value = false; await loadBindings() }

function confirmDeleteBinding(b) { deleteBindingTarget.value = b; showBindingConfirm.value = true }
async function doDeleteBinding() {
  await dpApi.deleteBinding(props.id, deleteBindingTarget.value.id)
  await loadBindings()
}

function coerceWriteValue(raw) {
  if (dp.value?.data_type === 'BOOLEAN') return raw === true || raw === 'true' || raw === '1' || raw === 1
  if (dp.value?.data_type === 'INTEGER') return Number.parseInt(raw, 10)
  if (dp.value?.data_type === 'FLOAT') return Number.parseFloat(raw)
  return raw
}

async function writeDetailValue(raw) {
  writeBusy.value = true
  writeFeedback.value = null
  try {
    const value = coerceWriteValue(raw)
    if (['INTEGER', 'FLOAT'].includes(dp.value?.data_type) && Number.isNaN(value)) {
      throw new Error('Bitte eine gültige Zahl eingeben.')
    }
    await dpStore.writeValue(props.id, value)
    if (dp.value) {
      dp.value.value = value
      dp.value.quality = 'good'
    }
    writeDraft.value = String(value)
    writeFeedback.value = { type: 'success', text: 'Wert geschrieben.' }
  } catch (err) {
    writeFeedback.value = {
      type: 'error',
      text: err?.message || 'Wert konnte nicht geschrieben werden.',
    }
  } finally {
    writeBusy.value = false
  }
}

function qualityVariant(q) {
  return q === 'good' ? 'success' : q === 'bad' ? 'danger' : q === 'uncertain' ? 'warning' : 'muted'
}
function qualityLabel(q) {
  return q === 'good' ? 'gut' : q === 'bad' ? 'schlecht' : q === 'uncertain' ? 'undefiniert' : q
}
</script>
