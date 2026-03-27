<template>
  <div class="flex flex-col gap-5">
    <div>
      <h2 class="text-xl font-bold text-slate-100">Einstellungen</h2>
      <p class="text-sm text-slate-500 mt-0.5">Benutzer, API Keys, Passwort, Import/Export</p>
    </div>

    <!-- Tabs -->
    <div class="flex gap-1 border-b border-slate-700/60">
      <button v-for="t in tabs" :key="t.id" @click="activeTab = t.id"
        :class="['px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
          activeTab === t.id ? 'text-blue-400 border-blue-500' : 'text-slate-400 border-transparent hover:text-slate-200']">
        {{ t.label }}
      </button>
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
          <thead><tr><th>Benutzername</th><th>Admin</th><th>Erstellt</th><th class="w-20"></th></tr></thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td class="font-medium">{{ u.username }}</td>
              <td><Badge :variant="u.is_admin ? 'warning' : 'muted'" size="xs">{{ u.is_admin ? 'Admin' : 'User' }}</Badge></td>
              <td class="text-xs text-slate-500">{{ new Date(u.created_at).toLocaleDateString('de-CH') }}</td>
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
              <td class="text-xs text-slate-500">{{ new Date(k.created_at).toLocaleDateString('de-CH') }}</td>
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

    <!-- ── Import/Export ── -->
    <div v-if="activeTab === 'importexport'" class="flex flex-col gap-4 max-w-lg">
      <div class="card p-5 flex flex-col gap-3">
        <h3 class="font-semibold text-sm text-slate-100">Export</h3>
        <p class="text-sm text-slate-400">Alle DataPoints, Bindings und Adapter-Konfigurationen als JSON exportieren.</p>
        <button @click="doExport" class="btn-secondary">Export herunterladen</button>
      </div>
      <div class="card p-5 flex flex-col gap-3">
        <h3 class="font-semibold text-sm text-slate-100">Import</h3>
        <p class="text-sm text-slate-400">Konfiguration aus JSON importieren. Bestehende Einträge werden mit Upsert-Semantik aktualisiert.</p>
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
          <input v-model="userForm.password" type="password" class="input" required />
        </div>
        <div class="flex items-center gap-2">
          <input type="checkbox" id="isAdmin" v-model="userForm.is_admin" class="w-4 h-4 rounded" />
          <label for="isAdmin" class="text-sm text-slate-300">Admin-Rechte</label>
        </div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showCreateUser = false" class="btn-secondary">Abbrechen</button>
          <button type="submit" class="btn-primary">Erstellen</button>
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
import { ref, reactive, onMounted } from 'vue'
import { authApi, configApi } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import Badge          from '@/components/ui/Badge.vue'
import Spinner        from '@/components/ui/Spinner.vue'
import Modal          from '@/components/ui/Modal.vue'
import ConfirmDialog  from '@/components/ui/ConfirmDialog.vue'

const auth = useAuthStore()
const activeTab = ref('password')

const tabs = [
  { id: 'password',     label: 'Passwort' },
  ...(auth.isAdmin ? [{ id: 'users', label: 'Benutzer' }] : []),
  { id: 'apikeys',      label: 'API Keys' },
  { id: 'importexport', label: 'Import / Export' },
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
const userForm    = reactive({ username: '', password: '', is_admin: false })

async function loadUsers() {
  usersLoading.value = true
  try { const { data } = await authApi.listUsers(); users.value = data }
  finally { usersLoading.value = false }
}
function openCreateUser() { userForm.username = ''; userForm.password = ''; userForm.is_admin = false; showCreateUser.value = true }
async function doCreateUser() {
  await authApi.createUser({ username: userForm.username, password: userForm.password, is_admin: userForm.is_admin })
  showCreateUser.value = false; await loadUsers()
}
function confirmDeleteUser(u) { deleteUserTarget.value = u; showUserConfirm.value = true }
async function doDeleteUser() { await authApi.deleteUser(deleteUserTarget.value.username); await loadUsers() }

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

// ── Import / Export ────────────────────────────────────────────────────────
const importResult = ref(null)

async function doExport() {
  const { data } = await configApi.export()
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a'); a.href = url; a.download = 'opentws-config.json'; a.click()
  URL.revokeObjectURL(url)
}
async function onImportFile(e) {
  const file = e.target.files[0]; if (!file) return
  const text = await file.text()
  try {
    const payload = JSON.parse(text)
    const { data } = await configApi.import(payload)
    importResult.value = { ok: true, text: `Import OK: ${data.datapoints_created} DP, ${data.bindings_created} Bindings` }
  } catch (err) {
    importResult.value = { ok: false, text: err.response?.data?.detail ?? 'Import fehlgeschlagen' }
  }
}

onMounted(async () => {
  if (auth.isAdmin) await loadUsers()
  await loadKeys()
})
</script>
