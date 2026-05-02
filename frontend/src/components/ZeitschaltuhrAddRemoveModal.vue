<script setup lang="ts">
/**
 * ZeitschaltuhrAddRemoveModal — Schaltpunkte verwalten.
 *
 * mode="full"        — Hinzufügen + Bearbeiten + Aktivieren/Deaktivieren + Löschen
 * mode="restricted"  — Bearbeiten + Aktivieren/Deaktivieren (kein Hinzufügen/Löschen)
 * mode="minimal"     — nur Aktivieren/Deaktivieren
 */
import { ref, onMounted } from 'vue'
import { datapoints as dpApi } from '@/api/client'
import type { BindingOut } from '@/api/client'
import ZeitschaltuhrBindingModal from '@/components/ZeitschaltuhrBindingModal.vue'

const props = defineProps<{
  datapointId: string
  instanceId:  string
  mode:        'full' | 'restricted' | 'minimal'
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

// ── State ─────────────────────────────────────────────────────────────────────

const loading   = ref(true)
const saving    = ref(false)
const errorMsg  = ref('')
const bindings  = ref<BindingOut[]>([])

const editingBinding   = ref<BindingOut | null>(null)
const pendingDeleteId  = ref<string | null>(null)

// ── Load ──────────────────────────────────────────────────────────────────────

onMounted(load)

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    bindings.value = await dpApi.listBindings(props.datapointId)
  } catch {
    errorMsg.value = 'Verknüpfungen konnten nicht geladen werden.'
  } finally {
    loading.value = false
  }
}

// ── Toggle enabled ────────────────────────────────────────────────────────────

async function toggleEnabled(b: BindingOut) {
  saving.value = true
  errorMsg.value = ''
  try {
    const updated = await dpApi.updateBinding(props.datapointId, String(b.id), { enabled: !b.enabled })
    const idx = bindings.value.findIndex((x) => x.id === b.id)
    if (idx >= 0) bindings.value[idx] = updated
  } catch {
    errorMsg.value = 'Fehler beim Ändern des Aktivierungsstatus.'
  } finally {
    saving.value = false
  }
}

// ── Delete (full mode only) ───────────────────────────────────────────────────

function requestDelete(b: BindingOut) {
  pendingDeleteId.value = String(b.id)
}

function cancelDelete() {
  pendingDeleteId.value = null
}

async function confirmDelete(b: BindingOut) {
  pendingDeleteId.value = null
  saving.value = true
  errorMsg.value = ''
  try {
    await dpApi.deleteBinding(props.datapointId, String(b.id))
    bindings.value = bindings.value.filter((x) => x.id !== b.id)
  } catch {
    errorMsg.value = 'Fehler beim Löschen des Schaltpunkts.'
  } finally {
    saving.value = false
  }
}

// ── Add (full mode only) ──────────────────────────────────────────────────────

async function addBinding() {
  saving.value = true
  errorMsg.value = ''
  try {
    const created = await dpApi.createBinding(props.datapointId, {
      adapter_instance_id: props.instanceId,
      direction: 'SOURCE',
      config: {},
      enabled: true,
    })
    bindings.value.push(created)
    // Direkt den neuen Schaltpunkt zum Bearbeiten öffnen
    editingBinding.value = created
  } catch {
    errorMsg.value = 'Fehler beim Erstellen des Schaltpunkts.'
  } finally {
    saving.value = false
  }
}

// ── Edit ──────────────────────────────────────────────────────────────────────

function openEdit(b: BindingOut) {
  editingBinding.value = b
}

function onEditSaved(enabled: boolean) {
  if (editingBinding.value) {
    const idx = bindings.value.findIndex((x) => x.id === editingBinding.value!.id)
    if (idx >= 0) bindings.value[idx] = { ...bindings.value[idx], enabled }
  }
  editingBinding.value = null
  // Reload to get fresh config labels
  load()
}

// ── Label helper ──────────────────────────────────────────────────────────────

function bindingLabel(b: BindingOut): string {
  const c = b.config
  const type = (c.timer_type as string | undefined) ?? 'daily'
  const ref  = (c.time_ref   as string | undefined) ?? 'absolute'
  const val  = (c.value      as string | undefined) ?? '?'

  let timeStr = ''
  if (ref === 'absolute') {
    const h = String((c.hour   as number | undefined) ?? 0).padStart(2, '0')
    const m = String((c.minute as number | undefined) ?? 0).padStart(2, '0')
    timeStr = `${h}:${m}`
  } else if (ref === 'sunrise')      timeStr = 'Sonnenaufgang'
  else if (ref === 'sunset')         timeStr = 'Sonnenuntergang'
  else if (ref === 'solar_noon')     timeStr = 'Sonnenmittag'
  else if (ref === 'solar_altitude') timeStr = `Sonne ${c.solar_altitude_deg ?? '?'}°`

  const typeStr = type === 'meta' ? 'Meta' : type === 'annual' ? 'jährlich' : type === 'holiday' ? 'Feiertag' : 'täglich'
  return `${typeStr} ${timeStr} → ${val}`.trim()
}

const btnBase = 'px-2 py-1 rounded text-xs font-medium transition-colors disabled:opacity-50 flex-shrink-0'
</script>

<template>
  <!-- Overlay -->
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
    @click.self="emit('close')"
  >
    <!-- Dialog -->
    <div class="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl shadow-2xl w-full max-w-md max-h-[90vh] flex flex-col">

      <!-- Header -->
      <div class="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
        <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100">
          🕐 Schaltpunkte
          <span class="ml-1 text-xs font-normal text-gray-400 dark:text-gray-500">
            {{ mode === 'full' ? '(Vollzugriff)' : mode === 'restricted' ? '(Eingeschränkt)' : '(Minimal)' }}
          </span>
        </h2>
        <button
          class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg leading-none"
          @click="emit('close')"
        >×</button>
      </div>

      <!-- Body -->
      <div class="flex-1 overflow-y-auto px-5 py-4 space-y-2">

        <div v-if="loading" class="text-sm text-gray-500 dark:text-gray-400 text-center py-6">Lade …</div>

        <template v-else>

          <div
            v-if="bindings.length === 0"
            class="text-sm text-gray-400 dark:text-gray-500 text-center py-4"
          >
            Keine Schaltpunkte vorhanden.
          </div>

          <!-- Binding-Liste -->
          <div
            v-for="b in bindings"
            :key="String(b.id)"
            class="rounded-lg border transition-colors"
            :class="pendingDeleteId === String(b.id)
              ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20'
              : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800'"
          >
            <!-- Normaler Zustand -->
            <div v-if="pendingDeleteId !== String(b.id)" class="flex items-center gap-2 px-3 py-2">
              <!-- Enable/Disable toggle -->
              <button
                type="button"
                :title="b.enabled ? 'Deaktivieren' : 'Aktivieren'"
                class="w-7 h-7 flex items-center justify-center rounded-full flex-shrink-0 transition-colors"
                :class="b.enabled
                  ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400 hover:bg-blue-200 dark:hover:bg-blue-900/70'
                  : 'bg-gray-200 dark:bg-gray-700 text-gray-400 hover:bg-gray-300 dark:hover:bg-gray-600'"
                :disabled="saving"
                @click="toggleEnabled(b)"
              >
                <svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                  <path v-if="b.enabled" fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.061l2.5 2.5a.75.75 0 001.137-.089l4-5.5z" clip-rule="evenodd"/>
                  <path v-else fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clip-rule="evenodd"/>
                </svg>
              </button>

              <!-- Label -->
              <span
                class="flex-1 text-xs truncate min-w-0"
                :class="b.enabled ? 'text-gray-800 dark:text-gray-200' : 'text-gray-400 dark:text-gray-500 line-through'"
              >{{ bindingLabel(b) }}</span>

              <!-- Edit button (full + restricted) -->
              <button
                v-if="mode !== 'minimal'"
                type="button"
                title="Bearbeiten"
                :class="[btnBase, 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-blue-100 dark:hover:bg-blue-900/40 hover:text-blue-600 dark:hover:text-blue-400 border border-gray-200 dark:border-gray-600']"
                :disabled="saving"
                @click="openEdit(b)"
              >✏️</button>

              <!-- Delete button (full only) — öffnet Bestätigung -->
              <button
                v-if="mode === 'full'"
                type="button"
                title="Löschen"
                :class="[btnBase, 'bg-red-50 dark:bg-red-900/20 text-red-500 hover:bg-red-100 dark:hover:bg-red-900/40 border border-red-200 dark:border-red-800']"
                :disabled="saving"
                @click="requestDelete(b)"
              >×</button>
            </div>

            <!-- Bestätigungs-Zustand -->
            <div v-else class="flex items-center gap-2 px-3 py-2">
              <span class="flex-1 text-xs text-red-600 dark:text-red-400 font-medium truncate min-w-0">
                Löschen: {{ bindingLabel(b) }}
              </span>
              <button
                type="button"
                :class="[btnBase, 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 border border-gray-200 dark:border-gray-600']"
                @click="cancelDelete"
              >Nein</button>
              <button
                type="button"
                :class="[btnBase, 'bg-red-600 hover:bg-red-500 text-white border border-red-600']"
                :disabled="saving"
                @click="confirmDelete(b)"
              >Ja</button>
            </div>
          </div>

          <p v-if="errorMsg" class="text-xs text-red-400 pt-1">{{ errorMsg }}</p>

        </template>
      </div><!-- /body -->

      <!-- Footer -->
      <div class="flex justify-between items-center gap-2 px-5 py-3 border-t border-gray-200 dark:border-gray-700 flex-shrink-0">
        <!-- Add button (full only) -->
        <button
          v-if="mode === 'full'"
          :class="[btnBase, 'px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-sm']"
          :disabled="saving || loading"
          @click="addBinding"
        >
          {{ saving ? '…' : '+ Neuer Schaltpunkt' }}
        </button>
        <span v-else />

        <button
          class="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 rounded"
          @click="emit('close')"
        >Schließen</button>
      </div>

    </div>
  </div>

  <!-- Edit-Modal (eingebettet, Teleport ins body) -->
  <Teleport to="body">
    <ZeitschaltuhrBindingModal
      v-if="editingBinding"
      :datapoint-id="props.datapointId"
      :binding-id="String(editingBinding.id)"
      @close="editingBinding = null"
      @saved="onEditSaved"
    />
  </Teleport>
</template>
