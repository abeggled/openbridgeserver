<template>
  <div class="flex flex-col gap-5">
    <!-- Demo-Modus Banner -->
    <div v-if="isDemo" class="flex items-center gap-3 p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg text-sm text-amber-600 dark:text-amber-400">
      <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m0 0v2m0-2h2m-2 0H10m2-11a7 7 0 110 14A7 7 0 0112 4z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v4"/></svg>
      Demo-Modus — Ansicht ist schreibgeschützt.
    </div>

    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">Adapter Instanzen</h2>
        <p class="text-sm text-slate-500 mt-0.5">Protokoll-Adapter konfigurieren und verwalten</p>
      </div>
      <button v-if="!isDemo" @click="openCreate" class="btn-primary btn-sm" data-testid="btn-new-instance">
        + Neue Instanz
      </button>
    </div>

    <div v-if="store.loading" class="flex justify-center py-20"><Spinner size="lg" /></div>

    <div v-else class="flex flex-col gap-4">

      <!-- Neue Instanz erstellen -->
      <div v-if="creating" class="card border border-blue-500/40">
        <div class="card-header">
          <h3 class="font-semibold text-slate-800 dark:text-slate-100">Neue Instanz erstellen</h3>
          <button @click="cancelCreate" class="btn-icon">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="p-5 flex flex-col gap-4">
          <div class="grid grid-cols-2 gap-4">
            <div class="form-group">
              <label class="label">Adapter-Typ *</label>
              <select v-model="newForm.adapter_type" class="input" required @change="onTypeChange" data-testid="select-adapter-type">
                <option value="">Typ wählen …</option>
                <option v-for="t in availableTypes" :key="t" :value="t">{{ t }}</option>
              </select>
              <p v-if="availableTypesErr" class="text-xs text-red-400 mt-1">Adapter-Typen konnten nicht geladen werden.</p>
            </div>
            <div class="form-group">
              <label class="label">Name *</label>
              <input v-model="newForm.name" type="text" class="input" placeholder="z.B. KNX Erdgeschoss" data-testid="input-instance-name" />
            </div>
          </div>

          <!-- Schema-based config form -->
          <div v-if="newForm.adapter_type && newSchema">
            <label class="label mb-2">Konfiguration</label>
            <SchemaForm :schema="newSchema" v-model="newForm.config" />
          </div>
          <div v-else-if="newForm.adapter_type && schemaLoading" class="flex items-center gap-2 text-sm text-slate-500">
            <Spinner size="xs" /> Schema wird geladen…
          </div>

          <div v-if="createError" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
            {{ createError }}
          </div>
          <div class="flex gap-3">
            <button @click="cancelCreate" class="btn-secondary btn-sm">Abbrechen</button>
            <button @click="submitCreate" class="btn-primary btn-sm" :disabled="creating === 'saving'" data-testid="btn-save-instance">
              <Spinner v-if="creating === 'saving'" size="xs" color="white" />
              Erstellen
            </button>
          </div>
        </div>
      </div>

      <!-- Bestehende Instanzen -->
      <div v-if="store.instances.length === 0 && !creating" class="card p-8 text-center text-slate-500">
        Keine Adapter-Instanzen konfiguriert. Klicke auf „+ Neue Instanz" um zu beginnen.
      </div>

      <div v-for="a in store.instances" :key="a.id" class="card" :data-testid="`adapter-row-${a.id}`">
        <!-- Card Header -->
        <div class="card-header">
          <div class="flex items-center gap-3 min-w-0">
            <span :class="['w-3 h-3 rounded-full shrink-0', a.connected ? 'bg-green-400' : a.running ? 'bg-amber-400 animate-pulse' : 'bg-slate-600']" />
            <h3 class="font-semibold text-slate-800 dark:text-slate-100 truncate">{{ a.name }}</h3>
            <Badge variant="info" size="xs">{{ a.adapter_type }}</Badge>
            <Badge :variant="a.connected ? 'success' : a.running ? 'warning' : 'muted'" size="xs">
              {{ a.connected ? 'Verbunden' : a.running ? 'Läuft' : 'Inaktiv' }}
            </Badge>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <button @click="toggleExpand(a)" class="btn-icon" :data-testid="`btn-expand-${a.id}`">
              <svg class="w-4 h-4 transition-transform" :class="expanded[a.id] ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
              </svg>
            </button>
          </div>
        </div>

        <!-- Kurzinfo -->
        <div class="px-5 py-2 flex gap-4 text-sm text-slate-500">
          <span>Verknüpfungen: <span class="text-slate-600 dark:text-slate-300 font-medium">{{ a.bindings }}</span></span>
          <span v-if="!a.registered" class="text-amber-400">⚠ Typ nicht registriert</span>
        </div>

        <!-- Expanded Config Panel -->
        <div v-if="expanded[a.id]" class="border-t border-slate-200 dark:border-slate-700/60 p-5 flex flex-col gap-4">
          <div :class="{ 'pointer-events-none select-none opacity-50': isDemo }">
            <div class="form-group">
              <label class="label">Name</label>
              <input v-model="drafts[a.id].name" type="text" class="input" />
            </div>

            <!-- Schema-based config form -->
            <div v-if="schemas[a.adapter_type]" class="mt-4">
              <label class="label mb-2">Konfiguration</label>
              <SchemaForm :schema="schemas[a.adapter_type]" v-model="drafts[a.id].config" />
            </div>
            <div v-else class="flex items-center gap-2 text-sm text-slate-500 mt-4">
              <Spinner size="xs" /> Schema wird geladen…
            </div>

            <div class="flex items-center gap-2 mt-4">
              <input type="checkbox" :id="'enabled-' + a.id" v-model="drafts[a.id].enabled" class="w-4 h-4 rounded" />
              <label :for="'enabled-' + a.id" class="text-sm text-slate-600 dark:text-slate-300">Aktiviert</label>
            </div>
          </div>

          <!-- Feedback -->
          <div v-if="feedback[a.id]" :class="[
            'flex items-center gap-2 p-3 rounded-lg text-sm',
            feedback[a.id].success ? 'bg-green-500/10 border border-green-500/30 text-green-400' : 'bg-red-500/10 border border-red-500/30 text-red-400'
          ]">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="feedback[a.id].success" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
            {{ feedback[a.id].detail }}
          </div>

          <div v-if="!isDemo" class="flex gap-3 flex-wrap">
            <button @click="testConnection(a)" class="btn-secondary btn-sm" :disabled="busy[a.id] === 'test'"
              title="Prüft die Verbindung mit der aktuellen Konfiguration ohne zu speichern">
              <Spinner v-if="busy[a.id] === 'test'" size="xs" color="slate" />
              Verbindung testen
            </button>
            <button @click="saveInstance(a)" class="btn-primary btn-sm" :disabled="busy[a.id] === 'save'"
              title="Speichert Änderungen und verbindet den Adapter neu">
              <Spinner v-if="busy[a.id] === 'save'" size="xs" color="white" />
              Speichern
            </button>
            <button @click="restartInstance(a)" class="btn-secondary btn-sm" :disabled="busy[a.id] === 'restart'"
              title="Verbindet den Adapter neu ohne die Konfiguration zu ändern">
              <Spinner v-if="busy[a.id] === 'restart'" size="xs" color="slate" />
              Neu verbinden
            </button>
            <button @click="confirmDelete(a)" class="ml-auto btn-danger btn-sm" :disabled="busy[a.id] === 'delete'"
              title="Löscht diese Instanz und alle zugehörigen Verknüpfungen unwiderruflich"
              data-testid="btn-delete-instance">
              <Spinner v-if="busy[a.id] === 'delete'" size="xs" color="white" />
              Löschen
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Löschen bestätigen -->
    <ConfirmDialog
      v-model="showDeleteConfirm"
      :title="deleteTarget ? `Instanz '${deleteTarget.name}' löschen?` : ''"
      message="Alle Verknüpfungen dieser Instanz werden ebenfalls gelöscht. Diese Aktion kann nicht rückgängig gemacht werden."
      confirm-label="Löschen"
      @confirm="executeDelete"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { adapterApi } from '@/api/client'
import { useAdapterStore } from '@/stores/adapters'
import { useAuthStore } from '@/stores/auth'
import Badge         from '@/components/ui/Badge.vue'
import Spinner       from '@/components/ui/Spinner.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import SchemaForm    from '@/components/adapters/SchemaForm.vue'

const store          = useAdapterStore()
const auth           = useAuthStore()
const isDemo         = computed(() => auth.username === 'demo')
const expanded       = reactive({})
const drafts         = reactive({})   // id → { name, config, enabled }
const feedback       = reactive({})   // id → { success, detail }
const busy           = reactive({})   // id → 'test' | 'save' | 'restart' | null
const schemas        = reactive({})   // adapter_type → JSON Schema

// Neue Instanz erstellen
const creating          = ref(false)     // false | true | 'saving'
const availableTypes    = ref([])
const availableTypesErr = ref(false)
const newForm           = reactive({ adapter_type: '', name: '', config: {} })
const newSchema         = ref(null)
const schemaLoading     = ref(false)
const createError       = ref(null)

// Löschen
const deleteTarget      = ref(null)
const showDeleteConfirm = ref(false)

// ------------------------------------------------------------------

onMounted(async () => {
  await store.fetchAdapters()
  initDrafts()
  try {
    availableTypes.value = await store.fetchTypes()
  } catch {
    availableTypesErr.value = true
  }
})

function initDrafts() {
  for (const a of store.instances) {
    if (!drafts[a.id]) {
      drafts[a.id] = {
        name:    a.name,
        config:  { ...a.config },
        enabled: a.enabled,
      }
    }
  }
}

// ---------- Schema laden ----------

async function loadSchema(adapterType) {
  if (schemas[adapterType]) return schemas[adapterType]
  try {
    const { data } = await adapterApi.schema(adapterType)
    schemas[adapterType] = data
    return data
  } catch {
    return null
  }
}

// ---------- Expand / collapse ----------

async function toggleExpand(a) {
  expanded[a.id] = !expanded[a.id]
  if (expanded[a.id]) {
    await loadSchema(a.adapter_type)
  }
}

// ---------- Neue Instanz: Typ-Wechsel ----------

async function onTypeChange() {
  newForm.config = {}
  newSchema.value = null
  if (!newForm.adapter_type) return
  schemaLoading.value = true
  try {
    const schema = await loadSchema(newForm.adapter_type)
    newSchema.value = schema
    // Pre-fill with schema defaults
    if (schema?.properties) {
      const defaults = {}
      for (const [key, prop] of Object.entries(schema.properties)) {
        if ('default' in prop) defaults[key] = prop.default
      }
      newForm.config = defaults
    }
  } finally {
    schemaLoading.value = false
  }
}

// ---------- Neu erstellen ----------

async function openCreate() {
  creating.value = true
  newForm.adapter_type = ''
  newForm.name = ''
  newForm.config = {}
  newSchema.value = null
  createError.value = null
}

function cancelCreate() {
  creating.value = false
}

async function submitCreate() {
  createError.value = null
  if (!newForm.adapter_type || !newForm.name.trim()) {
    createError.value = 'Bitte Typ und Name ausfüllen.'
    return
  }
  creating.value = 'saving'
  try {
    const inst = await store.createInstance(newForm.adapter_type, newForm.name.trim(), newForm.config)
    drafts[inst.id] = { name: inst.name, config: { ...inst.config }, enabled: inst.enabled }
    creating.value = false
  } catch (e) {
    createError.value = e.response?.data?.detail ?? 'Fehler beim Erstellen'
    creating.value = true
  }
}

// ---------- Verbindung testen ----------

async function testConnection(a) {
  busy[a.id] = 'test'
  delete feedback[a.id]
  try {
    const result = await store.testInstance(a.id, drafts[a.id].config)
    feedback[a.id] = result
  } catch (e) {
    feedback[a.id] = { success: false, detail: e.response?.data?.detail ?? 'Fehler' }
  } finally {
    busy[a.id] = null
  }
}

// ---------- Speichern ----------

async function saveInstance(a) {
  busy[a.id] = 'save'
  delete feedback[a.id]
  try {
    await store.updateInstance(a.id, {
      name:    drafts[a.id].name,
      config:  drafts[a.id].config,
      enabled: drafts[a.id].enabled,
    })
    feedback[a.id] = { success: true, detail: 'Gespeichert und neu verbunden' }
  } catch (e) {
    feedback[a.id] = { success: false, detail: e.response?.data?.detail ?? 'Fehler' }
  } finally {
    busy[a.id] = null
  }
}

// ---------- Neu verbinden ----------

async function restartInstance(a) {
  busy[a.id] = 'restart'
  delete feedback[a.id]
  try {
    await store.restartInstance(a.id)
    feedback[a.id] = { success: true, detail: 'Verbindung neu aufgebaut' }
  } catch (e) {
    feedback[a.id] = { success: false, detail: e.response?.data?.detail ?? 'Fehler' }
  } finally {
    busy[a.id] = null
  }
}

// ---------- Löschen ----------

function confirmDelete(a) {
  deleteTarget.value = a
  showDeleteConfirm.value = true
}

async function executeDelete() {
  const a = deleteTarget.value
  showDeleteConfirm.value = false
  deleteTarget.value = null
  if (!a) return
  busy[a.id] = 'delete'
  try {
    await store.deleteInstance(a.id)
    delete expanded[a.id]
    delete drafts[a.id]
    delete feedback[a.id]
  } catch (e) {
    feedback[a.id] = { success: false, detail: e.response?.data?.detail ?? 'Löschen fehlgeschlagen' }
  } finally {
    busy[a.id] = null
  }
}
</script>
