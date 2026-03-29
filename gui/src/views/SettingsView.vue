<template>
  <div class="flex flex-col gap-5">
    <div>
      <h2 class="text-xl font-bold text-slate-100">Einstellungen</h2>
      <p class="text-sm text-slate-500 mt-0.5">Benutzer, API Keys, Passwort, Sicherung</p>
    </div>

    <!-- Tabs -->
    <div class="flex gap-1 border-b border-slate-700/60">
      <button v-for="t in tabs" :key="t.id" @click="activeTab = t.id"
        :class="['px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
          activeTab === t.id ? 'text-blue-400 border-blue-500' : 'text-slate-400 border-transparent hover:text-slate-200']">
        {{ t.label }}
      </button>
    </div>

    <!-- ── Allgemein ── -->
    <div v-if="activeTab === 'general'" class="flex flex-col gap-4 max-w-md">

      <!-- Zeitzone -->
      <div class="card">
        <div class="card-header"><h3 class="font-semibold text-sm text-slate-100">Allgemeine Einstellungen</h3></div>
        <div class="card-body flex flex-col gap-4">
          <div class="form-group">
            <label class="label">Zeitzone</label>
            <p class="text-xs text-slate-500 mb-2">Alle Zeitangaben im System werden in dieser Zeitzone dargestellt.</p>
            <!-- Custom dropdown trigger -->
            <div class="relative" ref="tzDropdownRef">
              <button type="button" @click="tzDropdownOpen = !tzDropdownOpen"
                class="input text-sm w-full text-left flex items-center justify-between gap-2">
                <span class="font-mono text-slate-200 truncate">{{ tzSelected }}</span>
                <svg class="w-4 h-4 text-slate-400 shrink-0 transition-transform" :class="tzDropdownOpen ? 'rotate-180' : ''"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                </svg>
              </button>
              <!-- Dropdown panel -->
              <div v-if="tzDropdownOpen"
                class="absolute z-50 left-0 right-0 mt-1 bg-slate-800 border border-slate-600 rounded-lg shadow-xl overflow-hidden">
                <div class="p-2 border-b border-slate-700">
                  <input ref="tzSearchInputRef" v-model="tzSearch" type="text"
                    class="input text-sm w-full"
                    placeholder="Suchen … z.B. Zurich, Berlin, UTC"
                    @keydown.escape="tzDropdownOpen = false"
                    @keydown.enter.prevent="selectFirstTz" />
                </div>
                <div class="max-h-52 overflow-y-auto">
                  <button v-for="tz in filteredTimezones" :key="tz" type="button"
                    @click="selectTz(tz)"
                    :class="['w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-slate-700 transition-colors',
                      tz === tzSelected ? 'text-teal-400 bg-slate-700/50' : 'text-slate-300']">
                    {{ tz }}
                  </button>
                  <div v-if="!filteredTimezones.length" class="px-3 py-3 text-xs text-slate-500 text-center">Keine Treffer</div>
                </div>
              </div>
            </div>
          </div>
          <div v-if="tzMsg" :class="['p-3 rounded-lg text-sm', tzMsg.ok ? 'bg-green-500/10 text-green-400 border border-green-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30']">{{ tzMsg.text }}</div>
          <button @click="saveTz" class="btn-primary" :disabled="tzSaving">
            <Spinner v-if="tzSaving" size="sm" color="white" />
            Speichern
          </button>
        </div>
      </div>

      <!-- KNX Projekt Import -->
      <div class="card p-5 flex flex-col gap-3">
        <div class="flex items-center gap-2">
          <h3 class="font-semibold text-sm text-slate-100">KNX Projekt importieren</h3>
          <span class="text-xs text-slate-500 bg-slate-700/50 px-2 py-0.5 rounded">.knxproj</span>
        </div>
        <p class="text-sm text-slate-400">
          ETS-Projektdatei importieren. Alle Gruppenadressen (GA, Name, DPT) werden gespeichert,
          stehen im Binding-Formular als Suchvorschläge zur Verfügung und werden in der Sicherung mitgesichert.
        </p>
        <div class="flex flex-col gap-2">
          <input type="file" accept=".knxproj" @change="onKnxprojFile"
            class="text-sm text-slate-400 file:btn-secondary file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:text-xs file:border-0 file:cursor-pointer" />
          <div class="form-group">
            <label class="label">Projektpasswort <span class="text-slate-600 font-normal">(optional)</span></label>
            <input v-model="knxPassword" type="password" class="input text-sm" placeholder="Nur bei passwortgeschützten Projekten" autocomplete="off" />
          </div>
          <div class="flex items-center gap-3">
            <button @click="doKnxImport" class="btn-primary btn-sm" :disabled="!knxFile || knxImporting">
              <Spinner v-if="knxImporting" size="sm" color="white" />
              Importieren
            </button>
            <button v-if="knxGaCount > 0" @click="doClearKnxGA" class="btn-secondary btn-sm text-red-400 hover:text-red-300">
              {{ knxGaCount }} GAs löschen
            </button>
          </div>
        </div>
        <div v-if="knxResult" :class="['p-3 rounded-lg text-sm', knxResult.ok ? 'bg-green-500/10 text-green-400 border border-green-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30']">
          {{ knxResult.text }}
        </div>
      </div>
    </div>

    <!-- ── Passwort ── -->
    <div v-if="activeTab === 'password'" class="card max-w-md">
      <div class="card-header"><h3 class="font-semibold text-sm text-slate-100">Passwort ändern</h3></div>
      <div class="card-body">
        <form @submit.prevent="changePassword" class="flex flex-col gap-4">
          <div class="form-group">
            <label class="label">Aktuelles Passwort</label>
            <input v-model="pwForm.current" type="password" class="input" required autocomplete="current-password" />
          </div>
          <div class="form-group">
            <label class="label">Neues Passwort</label>
            <input v-model="pwForm.new1" type="password" class="input" required autocomplete="new-password" />
          </div>
          <div class="form-group">
            <label class="label">Neues Passwort wiederholen</label>
            <input v-model="pwForm.new2" type="password" class="input" required autocomplete="new-password" />
          </div>
          <div v-if="pwMsg" :class="['p-3 rounded-lg text-sm', pwMsg.ok ? 'bg-green-500/10 text-green-400 border border-green-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30']">{{ pwMsg.text }}</div>
          <button type="submit" class="btn-primary" :disabled="pwSaving">
            <Spinner v-if="pwSaving" size="sm" color="white" />
            Passwort ändern
          </button>
        </form>
      </div>
    </div>

    <!-- ── Benutzer (Admin only) ── -->
    <div v-if="activeTab === 'users' && auth.isAdmin">
      <div class="flex items-center gap-3 mb-4">
        <span class="flex-1 text-sm text-slate-400">{{ users.length }} Benutzer</span>
        <button @click="openCreateUser" class="btn-primary btn-sm">+ Benutzer</button>
      </div>
      <div class="card overflow-hidden">
        <div v-if="usersLoading" class="flex justify-center py-8"><Spinner /></div>
        <table v-else class="table">
          <thead><tr><th>Benutzername</th><th>Admin</th><th>MQTT</th><th>Erstellt</th><th class="w-20"></th></tr></thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td class="font-medium">{{ u.username }}</td>
              <td><Badge :variant="u.is_admin ? 'warning' : 'muted'" size="xs">{{ u.is_admin ? 'Admin' : 'User' }}</Badge></td>
              <td>
                <div class="flex items-center gap-1">
                  <Badge :variant="u.mqtt_enabled ? 'success' : 'muted'" size="xs">{{ u.mqtt_enabled ? 'Aktiv' : 'Aus' }}</Badge>
                  <button @click="openMqttPassword(u)" class="btn-icon text-slate-400 hover:text-blue-400" title="MQTT-Passwort setzen">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536M9 13l6.586-6.586a2 2 0 112.828 2.828L11.828 15.828a2 2 0 01-1.414.586H9v-2a2 2 0 01.586-1.414z"/></svg>
                  </button>
                  <button v-if="u.mqtt_enabled" @click="doDeleteMqttPassword(u)" class="btn-icon text-red-400 hover:text-red-300" title="MQTT deaktivieren">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                  </button>
                </div>
              </td>
              <td class="text-xs text-slate-500">{{ fmtDate(u.created_at) }}</td>
              <td>
                <button v-if="u.username !== auth.username" @click="confirmDeleteUser(u)" class="btn-icon text-red-400">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ── API Keys ── -->
    <div v-if="activeTab === 'apikeys'">
      <div class="flex items-center gap-3 mb-4">
        <span class="flex-1 text-sm text-slate-400">{{ apiKeys.length }} API Keys</span>
        <button @click="createApiKey" class="btn-primary btn-sm">+ API Key</button>
      </div>
      <div class="card overflow-hidden mb-4">
        <div v-if="keysLoading" class="flex justify-center py-8"><Spinner /></div>
        <table v-else class="table">
          <thead><tr><th>Name</th><th>Erstellt</th><th class="w-20"></th></tr></thead>
          <tbody>
            <tr v-for="k in apiKeys" :key="k.id">
              <td class="font-medium">{{ k.name }}</td>
              <td class="text-xs text-slate-500">{{ fmtDate(k.created_at) }}</td>
              <td><button @click="deleteApiKey(k.id)" class="btn-icon text-red-400"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg></button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <!-- New key secret display -->
      <div v-if="newKeySecret" class="p-4 bg-green-500/10 border border-green-500/30 rounded-lg">
        <p class="text-sm text-green-400 font-medium mb-2">⚠ Key nur jetzt sichtbar — jetzt kopieren!</p>
        <code class="font-mono text-xs text-green-300 break-all select-all">{{ newKeySecret }}</code>
      </div>
    </div>

    <!-- ── Sicherung / Wiederherstellung ── -->
    <div v-if="activeTab === 'importexport'" class="flex flex-col gap-4 max-w-lg">
      <div class="card p-5 flex flex-col gap-3">
        <h3 class="font-semibold text-sm text-slate-100">Sicherung erstellen</h3>
        <p class="text-sm text-slate-400">Alle DataPoints, Bindings, Adapter-Instanzen, KNX-Gruppenadressen und Logikblätter als JSON-Datei sichern.</p>
        <button @click="doExport" class="btn-secondary">Sicherung herunterladen</button>
      </div>
      <div class="card p-5 flex flex-col gap-3">
        <h3 class="font-semibold text-sm text-slate-100">Sicherung wiederherstellen</h3>
        <p class="text-sm text-slate-400">Sicherungsdatei einspielen. Bestehende Einträge werden aktualisiert, fehlende neu angelegt.</p>
        <input type="file" accept=".json" @change="onImportFile" class="text-sm text-slate-400 file:btn-secondary file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:text-xs file:border-0 file:cursor-pointer" />
        <div v-if="importResult" :class="['p-3 rounded-lg text-sm', importResult.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400']">{{ importResult.text }}</div>
      </div>
    </div>

    <!-- Modals -->
    <Modal v-model="showCreateUser" title="Neuer Benutzer" max-width="sm">
      <form @submit.prevent="doCreateUser" class="flex flex-col gap-4">
        <div class="form-group">
          <label class="label">Benutzername</label>
          <input v-model="userForm.username" type="text" class="input" required />
        </div>
        <div class="form-group">
          <label class="label">Passwort</label>
          <input v-model="userForm.password" type="password" class="input" required autocomplete="new-password" />
        </div>
        <div class="flex items-center gap-2">
          <input type="checkbox" id="isAdmin" v-model="userForm.is_admin" class="w-4 h-4 rounded" />
          <label for="isAdmin" class="text-sm text-slate-300">Admin-Rechte</label>
        </div>
        <div class="flex items-center gap-2">
          <input type="checkbox" id="mqttEnabled" v-model="userForm.mqtt_enabled" class="w-4 h-4 rounded" />
          <label for="mqttEnabled" class="text-sm text-slate-300">MQTT aktivieren</label>
        </div>
        <div v-if="userForm.mqtt_enabled" class="form-group">
          <label class="label">MQTT-Passwort</label>
          <input v-model="userForm.mqtt_password" type="password" class="input" autocomplete="new-password" placeholder="Leer = kein MQTT-Passwort" />
        </div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showCreateUser = false" class="btn-secondary">Abbrechen</button>
          <button type="submit" class="btn-primary">Erstellen</button>
        </div>
      </form>
    </Modal>

    <Modal v-model="showMqttPassword" title="MQTT-Passwort setzen" max-width="sm">
      <form @submit.prevent="doSetMqttPassword" class="flex flex-col gap-4">
        <p class="text-sm text-slate-400">Benutzer: <span class="text-slate-200 font-medium">{{ mqttTarget?.username }}</span></p>
        <div class="form-group">
          <label class="label">Neues MQTT-Passwort</label>
          <input v-model="mqttPasswordInput" type="password" class="input" required autocomplete="new-password" />
        </div>
        <div v-if="mqttMsg" :class="['p-3 rounded-lg text-sm', mqttMsg.ok ? 'bg-green-500/10 text-green-400 border border-green-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30']">{{ mqttMsg.text }}</div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showMqttPassword = false" class="btn-secondary">Abbrechen</button>
          <button type="submit" class="btn-primary" :disabled="mqttSaving">
            <Spinner v-if="mqttSaving" size="sm" color="white" />
            Speichern
          </button>
        </div>
      </form>
    </Modal>

    <Modal v-model="showNewKeyName" title="API Key Name" max-width="sm">
      <form @submit.prevent="doCreateKey" class="flex flex-col gap-4">
        <div class="form-group">
          <label class="label">Beschreibung / Name</label>
          <input v-model="newKeyName" type="text" class="input" placeholder="z.B. Home Assistant" required />
        </div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showNewKeyName = false" class="btn-secondary">Abbrechen</button>
          <button type="submit" class="btn-primary">Erstellen</button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="showUserConfirm" title="Benutzer löschen"
      :message="`Benutzer '${deleteUserTarget?.username}' wirklich löschen?`"
      confirm-label="Löschen" @confirm="doDeleteUser" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { authApi, configApi, knxprojApi } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import { useTz } from '@/composables/useTz'
import Badge          from '@/components/ui/Badge.vue'
import Spinner        from '@/components/ui/Spinner.vue'
import Modal          from '@/components/ui/Modal.vue'
import ConfirmDialog  from '@/components/ui/ConfirmDialog.vue'

const auth     = useAuthStore()
const settings = useSettingsStore()
const { fmtDate } = useTz()
const activeTab = ref('general')

// ── Timezone ──────────────────────────────────────────────────────────────
// Build full IANA timezone list from browser API (modern browsers support this)
const ALL_TIMEZONES = (() => {
  try {
    return Intl.supportedValuesOf('timeZone')
  } catch {
    return [
      'UTC', 'Europe/Zurich', 'Europe/Berlin', 'Europe/Vienna', 'Europe/London',
      'Europe/Paris', 'Europe/Rome', 'Europe/Amsterdam', 'Europe/Brussels',
      'Europe/Stockholm', 'Europe/Oslo', 'Europe/Copenhagen', 'Europe/Helsinki',
      'Europe/Warsaw', 'Europe/Prague', 'Europe/Budapest', 'Europe/Bucharest',
      'Europe/Athens', 'Europe/Istanbul', 'Europe/Moscow',
      'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
      'America/Anchorage', 'America/Honolulu', 'America/Toronto', 'America/Vancouver',
      'America/Sao_Paulo', 'America/Argentina/Buenos_Aires', 'America/Mexico_City',
      'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Seoul', 'Asia/Singapore', 'Asia/Dubai',
      'Asia/Kolkata', 'Asia/Bangkok', 'Asia/Jakarta', 'Asia/Hong_Kong',
      'Australia/Sydney', 'Australia/Melbourne', 'Australia/Perth',
      'Pacific/Auckland', 'Pacific/Fiji', 'Africa/Johannesburg', 'Africa/Cairo',
    ]
  }
})()

const tzSearch         = ref('')
const tzSelected       = ref(settings.timezone)
const tzSaving         = ref(false)
const tzMsg            = ref(null)
const tzDropdownOpen   = ref(false)
const tzDropdownRef    = ref(null)
const tzSearchInputRef = ref(null)

const filteredTimezones = computed(() => {
  const q = tzSearch.value.toLowerCase()
  if (!q) return ALL_TIMEZONES
  return ALL_TIMEZONES.filter(tz => tz.toLowerCase().includes(q))
})

function selectTz(tz) {
  tzSelected.value   = tz
  tzDropdownOpen.value = false
  tzSearch.value     = ''
}
function selectFirstTz() {
  if (filteredTimezones.value.length) selectTz(filteredTimezones.value[0])
}

// Auto-focus search input when dropdown opens
watch(tzDropdownOpen, async (open) => {
  if (open) {
    await nextTick()
    tzSearchInputRef.value?.focus()
  } else {
    tzSearch.value = ''
  }
})

// Close dropdown on outside click
function onOutsideClick(e) {
  if (tzDropdownRef.value && !tzDropdownRef.value.contains(e.target)) {
    tzDropdownOpen.value = false
  }
}

onMounted(async () => {
  if (!settings.loaded) await settings.load()
  tzSelected.value = settings.timezone
  document.addEventListener('mousedown', onOutsideClick)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', onOutsideClick)
})

async function saveTz() {
  tzSaving.value = true; tzMsg.value = null
  try {
    await settings.save(tzSelected.value)
    tzMsg.value = { ok: true, text: `Zeitzone auf «${tzSelected.value}» gesetzt` }
  } catch (e) {
    tzMsg.value = { ok: false, text: e.response?.data?.detail ?? 'Fehler beim Speichern' }
  } finally {
    tzSaving.value = false
  }
}

const tabs = [
  { id: 'general',      label: 'Allgemein' },
  { id: 'password',     label: 'Passwort' },
  ...(auth.isAdmin ? [{ id: 'users', label: 'Benutzer' }] : []),
  { id: 'apikeys',      label: 'API Keys' },
  { id: 'importexport', label: 'Sicherung' },
]

// ── Password ──────────────────────────────────────────────────────────────
const pwForm  = reactive({ current: '', new1: '', new2: '' })
const pwSaving = ref(false)
const pwMsg    = ref(null)

async function changePassword() {
  if (pwForm.new1 !== pwForm.new2) { pwMsg.value = { ok: false, text: 'Passwörter stimmen nicht überein' }; return }
  pwSaving.value = true; pwMsg.value = null
  try {
    await authApi.changePassword(pwForm.current, pwForm.new1)
    pwMsg.value = { ok: true, text: 'Passwort erfolgreich geändert' }
    pwForm.current = ''; pwForm.new1 = ''; pwForm.new2 = ''
  } catch (e) {
    pwMsg.value = { ok: false, text: e.response?.data?.detail ?? 'Fehler' }
  } finally {
    pwSaving.value = false
  }
}

// ── Users ──────────────────────────────────────────────────────────────────
const users       = ref([])
const usersLoading = ref(false)
const showCreateUser = ref(false)
const showUserConfirm = ref(false)
const deleteUserTarget = ref(null)
const userForm    = reactive({ username: '', password: '', is_admin: false, mqtt_enabled: false, mqtt_password: '' })

async function loadUsers() {
  usersLoading.value = true
  try { const { data } = await authApi.listUsers(); users.value = data }
  finally { usersLoading.value = false }
}
function openCreateUser() {
  userForm.username = ''; userForm.password = ''; userForm.is_admin = false
  userForm.mqtt_enabled = false; userForm.mqtt_password = ''
  showCreateUser.value = true
}
async function doCreateUser() {
  const payload = { username: userForm.username, password: userForm.password, is_admin: userForm.is_admin }
  if (userForm.mqtt_enabled && userForm.mqtt_password) {
    payload.mqtt_enabled = true
    payload.mqtt_password = userForm.mqtt_password
  }
  await authApi.createUser(payload)
  showCreateUser.value = false; await loadUsers()
}
function confirmDeleteUser(u) { deleteUserTarget.value = u; showUserConfirm.value = true }
async function doDeleteUser() { await authApi.deleteUser(deleteUserTarget.value.username); await loadUsers() }

// ── MQTT Password ──────────────────────────────────────────────────────────
const showMqttPassword = ref(false)
const mqttTarget       = ref(null)
const mqttPasswordInput = ref('')
const mqttSaving       = ref(false)
const mqttMsg          = ref(null)

function openMqttPassword(u) {
  mqttTarget.value = u; mqttPasswordInput.value = ''; mqttMsg.value = null
  showMqttPassword.value = true
}
async function doSetMqttPassword() {
  mqttSaving.value = true; mqttMsg.value = null
  try {
    await authApi.setMqttPassword(mqttTarget.value.username, mqttPasswordInput.value)
    mqttMsg.value = { ok: true, text: 'MQTT-Passwort gesetzt' }
    await loadUsers()
    setTimeout(() => { showMqttPassword.value = false }, 800)
  } catch (e) {
    mqttMsg.value = { ok: false, text: e.response?.data?.detail ?? 'Fehler' }
  } finally {
    mqttSaving.value = false
  }
}
async function doDeleteMqttPassword(u) {
  await authApi.deleteMqttPassword(u.username)
  await loadUsers()
}

// ── API Keys ───────────────────────────────────────────────────────────────
const apiKeys       = ref([])
const keysLoading   = ref(false)
const newKeySecret  = ref('')
const newKeyName    = ref('')
const showNewKeyName = ref(false)

async function loadKeys() {
  keysLoading.value = true
  try { const { data } = await authApi.listApiKeys(); apiKeys.value = data }
  catch { apiKeys.value = [] }
  finally { keysLoading.value = false }
}
function createApiKey() { newKeyName.value = ''; showNewKeyName.value = true }
async function doCreateKey() {
  const { data } = await authApi.createApiKey(newKeyName.value)
  newKeySecret.value = data.key
  showNewKeyName.value = false; await loadKeys()
}
async function deleteApiKey(id) { await authApi.deleteApiKey(id); await loadKeys() }

// ── Sicherung / Wiederherstellung ──────────────────────────────────────────
const importResult = ref(null)

async function doExport() {
  const { data } = await configApi.export()
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a'); a.href = url; a.download = 'opentws-backup.json'; a.click()
  URL.revokeObjectURL(url)
}
async function onImportFile(e) {
  const file = e.target.files[0]; if (!file) return
  const text = await file.text()
  try {
    const payload = JSON.parse(text)
    const { data } = await configApi.import(payload)
    const gaInfo    = data.knx_group_addresses_upserted > 0 ? `, ${data.knx_group_addresses_upserted} KNX-GAs` : ''
    const lgTotal   = (data.logic_graphs_created ?? 0) + (data.logic_graphs_updated ?? 0)
    const lgInfo    = lgTotal > 0 ? `, ${lgTotal} Logikblätter` : ''
    importResult.value = { ok: true, text: `Wiederherstellung OK: ${data.datapoints_created + data.datapoints_updated} DataPoints, ${data.bindings_created + data.bindings_updated} Bindings${gaInfo}${lgInfo}` }
  } catch (err) {
    importResult.value = { ok: false, text: err.response?.data?.detail ?? 'Import fehlgeschlagen' }
  }
}

// ── KNX Projekt Import ──────────────────────────────────────────────────────
const knxFile      = ref(null)
const knxPassword  = ref('')
const knxImporting = ref(false)
const knxResult    = ref(null)
const knxGaCount   = ref(0)

async function loadKnxGaCount() {
  try {
    const { data } = await knxprojApi.listGA({ size: 1 })
    knxGaCount.value = data.total || 0
  } catch { knxGaCount.value = 0 }
}

function onKnxprojFile(e) {
  knxFile.value  = e.target.files[0] || null
  knxResult.value = null
}

async function doKnxImport() {
  if (!knxFile.value) return
  knxImporting.value = true
  knxResult.value    = null
  try {
    const fd = new FormData()
    fd.append('file', knxFile.value)
    if (knxPassword.value) fd.append('password', knxPassword.value)
    const { data } = await knxprojApi.import(fd)
    knxResult.value = { ok: true, text: data.message }
    await loadKnxGaCount()
  } catch (err) {
    knxResult.value = { ok: false, text: err.response?.data?.detail ?? 'Import fehlgeschlagen' }
  } finally {
    knxImporting.value = false
  }
}

async function doClearKnxGA() {
  try {
    await knxprojApi.clearGA()
    knxGaCount.value = 0
    knxResult.value  = { ok: true, text: 'Alle Gruppenadressen gelöscht' }
  } catch {
    knxResult.value  = { ok: false, text: 'Fehler beim Löschen' }
  }
}

onMounted(async () => {
  if (auth.isAdmin) await loadUsers()
  await loadKeys()
  await loadKnxGaCount()
})
// Note: timezone onMounted is defined above (merged there)
</script>
