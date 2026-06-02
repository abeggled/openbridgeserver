<template>
  <div class="flex flex-col gap-5">
    <div>
      <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ $t('settings.title') }}</h2>
      <p class="text-sm text-slate-500 mt-0.5">{{ $t('settings.subtitle') }}</p>
    </div>

    <!-- Tabs -->
    <div class="flex gap-1 border-b border-slate-200 dark:border-slate-700/60">
      <button v-for="tab in tabs" :key="tab.id" @click="activeTab = tab.id"
        :class="['px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
          activeTab === tab.id && tab.id === 'dangerzone' ? 'text-red-500 dark:text-red-400 border-red-500' :
          activeTab === tab.id ? 'text-blue-500 dark:text-blue-400 border-blue-500' :
          tab.id === 'dangerzone' ? 'text-red-400/70 dark:text-red-400/60 border-transparent hover:text-red-400' :
          'text-slate-500 dark:text-slate-400 border-transparent hover:text-slate-700 dark:hover:text-slate-200']">
        {{ tab.label }}
      </button>
    </div>

    <!-- Demo-Modus Banner -->
    <div v-if="isDemo" class="flex items-center gap-3 p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg text-sm text-amber-600 dark:text-amber-400">
      <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v4m0 4h.01M12 3a9 9 0 110 18A9 9 0 0112 3z"/></svg>
      {{ $t('common.demoMode') }}
    </div>

    <!-- ── Allgemein ── -->
    <div v-if="activeTab === 'general'" class="flex flex-col gap-4 max-w-md">

      <!-- Zeitzone -->
      <div class="card" :class="{ 'pointer-events-none select-none opacity-60': isDemo }">
        <div class="card-header"><h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.general.title') }}</h3></div>
        <div class="card-body flex flex-col gap-4">
          <div class="form-group">
            <label class="label">{{ $t('settings.general.timezone') }}</label>
            <p class="text-xs text-slate-500 mb-2">{{ $t('settings.general.timezoneHint') }}</p>
            <!-- Custom dropdown trigger -->
            <div class="relative" ref="tzDropdownRef">
              <button type="button" @click="tzDropdownOpen = !tzDropdownOpen"
                class="input text-sm w-full text-left flex items-center justify-between gap-2">
                <span class="font-mono text-slate-700 dark:text-slate-200 truncate">{{ tzSelected }}</span>
                <svg class="w-4 h-4 text-slate-400 shrink-0 transition-transform" :class="tzDropdownOpen ? 'rotate-180' : ''"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                </svg>
              </button>
              <!-- Dropdown panel -->
              <div v-if="tzDropdownOpen"
                class="absolute z-50 left-0 right-0 mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-600 rounded-lg shadow-xl overflow-hidden">
                <div class="p-2 border-b border-slate-200 dark:border-slate-700">
                  <input ref="tzSearchInputRef" v-model="tzSearch" type="text"
                    class="input text-sm w-full"
                    :placeholder="$t('settings.general.timezonePlaceholder')"
                    @keydown.escape="tzDropdownOpen = false"
                    @keydown.enter.prevent="selectFirstTz" />
                </div>
                <div class="max-h-52 overflow-y-auto">
                  <button v-for="tz in filteredTimezones" :key="tz" type="button"
                    @click="selectTz(tz)"
                    :class="['w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors',
                      tz === tzSelected ? 'text-teal-600 dark:text-teal-400 bg-slate-100/80 dark:bg-slate-700/50' : 'text-slate-600 dark:text-slate-300']">
                    {{ tz }}
                  </button>
                  <div v-if="!filteredTimezones.length" class="px-3 py-3 text-xs text-slate-500 text-center">{{ $t('settings.general.noResults') }}</div>
                </div>
              </div>
            </div>
          </div>
          <div v-if="tzMsg" :class="['p-3 rounded-lg text-sm', tzMsg.ok ? 'bg-green-500/10 text-green-400 border border-green-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30']">{{ tzMsg.text }}</div>
          <button @click="saveTz" class="btn-primary" :disabled="tzSaving">
            <Spinner v-if="tzSaving" size="sm" color="white" />
            {{ $t('settings.general.save') }}
          </button>
        </div>
      </div>

      <!-- Erscheinungsbild -->
      <div class="card">
        <div class="card-header">
          <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.general.appearance') }}</h3>
        </div>
        <div class="card-body flex flex-col gap-3">
          <p class="text-sm text-slate-500">{{ $t('settings.general.appearanceHint') }}</p>
          <div class="flex flex-col gap-2">
            <label v-for="opt in themeOptions" :key="opt.value"
              :class="[
                'flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors',
                selectedTheme === opt.value
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-500/10'
                  : 'border-slate-200 dark:border-slate-700/60 hover:bg-slate-50 dark:hover:bg-slate-800/40'
              ]">
              <input type="radio" :value="opt.value" v-model="selectedTheme" class="accent-blue-500 shrink-0" />
              <div>
                <div class="text-sm font-medium text-slate-800 dark:text-slate-200">{{ opt.label }}</div>
                <div class="text-xs text-slate-500">{{ opt.desc }}</div>
              </div>
            </label>
          </div>
          <!-- Language switcher -->
          <div class="pt-2 border-t border-slate-200 dark:border-slate-700/60">
            <LocaleSwitcher />
          </div>
        </div>
      </div>
    </div>

    <!-- ── Passwort ── -->
    <div v-if="activeTab === 'password'" class="card max-w-md" :class="{ 'pointer-events-none select-none opacity-60': isDemo }">
      <div class="card-header"><h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.password.title') }}</h3></div>
      <div class="card-body">
        <form @submit.prevent="changePassword" class="flex flex-col gap-4">
          <div class="form-group">
            <label class="label">{{ $t('settings.password.currentPassword') }}</label>
            <input v-model="pwForm.current" type="password" class="input" required autocomplete="current-password" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('settings.password.newPassword') }}</label>
            <input v-model="pwForm.new1" type="password" class="input" required autocomplete="new-password" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('settings.password.confirmPassword') }}</label>
            <input v-model="pwForm.new2" type="password" class="input" required autocomplete="new-password" />
          </div>
          <div v-if="pwMsg" :class="['p-3 rounded-lg text-sm', pwMsg.ok ? 'bg-green-500/10 text-green-400 border border-green-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30']">{{ pwMsg.text }}</div>
          <button type="submit" class="btn-primary" :disabled="pwSaving">
            <Spinner v-if="pwSaving" size="sm" color="white" />
            {{ $t('settings.password.save') }}
          </button>
        </form>
      </div>
    </div>

    <!-- ── Benutzer (Admin only) ── -->
    <div v-if="activeTab === 'users' && (auth.isAdmin || isDemo)" :class="{ 'pointer-events-none select-none opacity-60': isDemo }">
      <div class="flex items-center gap-3 mb-4">
        <span class="flex-1 text-sm text-slate-400">{{ $t('settings.users.count', { n: users.length }) }}</span>
        <button @click="openCreateUser" class="btn-primary btn-sm">{{ $t('settings.users.addButton') }}</button>
      </div>
      <div class="card overflow-hidden">
        <div v-if="usersLoading" class="flex justify-center py-8"><Spinner /></div>
        <table v-else class="table">
          <thead><tr><th>{{ $t('settings.users.colUsername') }}</th><th>{{ $t('settings.users.colAdmin') }}</th><th>{{ $t('settings.users.colMqtt') }}</th><th>{{ $t('settings.users.colCreated') }}</th><th class="w-20"></th></tr></thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td class="font-medium">{{ u.username }}</td>
              <td><Badge :variant="u.is_admin ? 'warning' : 'muted'" size="xs">{{ u.is_admin ? 'Admin' : 'User' }}</Badge></td>
              <td>
                <div class="flex items-center gap-1">
                  <Badge :variant="u.mqtt_enabled ? 'success' : 'muted'" size="xs">{{ u.mqtt_enabled ? $t('settings.users.mqttActive') : $t('settings.users.mqttOff') }}</Badge>
                  <button @click="openMqttPassword(u)" class="btn-icon text-slate-400 hover:text-blue-400" :title="$t('settings.users.mqttSetTitle')">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536M9 13l6.586-6.586a2 2 0 112.828 2.828L11.828 15.828a2 2 0 01-1.414.586H9v-2a2 2 0 01.586-1.414z"/></svg>
                  </button>
                  <button v-if="u.mqtt_enabled" @click="doDeleteMqttPassword(u)" class="btn-icon text-red-400 hover:text-red-300" :title="$t('settings.users.mqttDisableTitle')">
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
    <div v-if="activeTab === 'apikeys'" :class="{ 'pointer-events-none select-none opacity-60': isDemo }">
      <div class="flex items-center gap-3 mb-4">
        <span class="flex-1 text-sm text-slate-400">{{ $t('settings.apikeys.count', { n: apiKeys.length }) }}</span>
        <button @click="createApiKey" class="btn-primary btn-sm">{{ $t('settings.apikeys.addButton') }}</button>
      </div>
      <div class="card overflow-hidden mb-4">
        <div v-if="keysLoading" class="flex justify-center py-8"><Spinner /></div>
        <table v-else class="table">
          <thead><tr><th>{{ $t('settings.apikeys.colName') }}</th><th>{{ $t('settings.apikeys.colCreated') }}</th><th class="w-20"></th></tr></thead>
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
        <p class="text-sm text-green-400 font-medium mb-2">{{ $t('settings.apikeys.newKeySecret') }}</p>
        <code class="font-mono text-xs text-green-700 dark:text-green-300 break-all select-all">{{ newKeySecret }}</code>
      </div>
    </div>

    <!-- ── Datenmanagement ── -->
    <div v-if="activeTab === 'importexport'" class="flex flex-col gap-4 max-w-lg" :class="{ 'pointer-events-none select-none opacity-60': isDemo }">

      <!-- Sicherung erstellen (download) -->
      <div class="card p-5 flex flex-col gap-3">
        <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.importexport.exportTitle') }}</h3>
        <p class="text-sm text-slate-400">{{ $t('settings.importexport.exportDesc') }}</p>
        <button @click="doExport" class="btn-secondary">{{ $t('settings.importexport.exportButton') }}</button>
      </div>

      <!-- Sicherung wiederherstellen (upload) -->
      <div class="card p-5 flex flex-col gap-3">
        <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.importexport.importTitle') }}</h3>
        <p class="text-sm text-slate-400">{{ $t('settings.importexport.importDesc') }}</p>
        <div class="flex items-center gap-3">
          <button type="button" class="btn-secondary btn-sm" @click="importFileInput.click()">{{ $t('common.chooseFile') }}</button>
          <span class="text-sm text-slate-400">{{ importFileName || $t('common.noFileSelected') }}</span>
          <input ref="importFileInput" type="file" accept=".json" @change="onImportFile" class="hidden" />
        </div>
        <div v-if="importResult" :class="['p-3 rounded-lg text-sm', importResult.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400']">{{ importResult.text }}</div>
      </div>

      <!-- Datenbanksicherung erstellen (download) -->
      <div class="card p-5 flex flex-col gap-3">
        <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.importexport.dbExportTitle') }}</h3>
        <p class="text-sm text-slate-400">{{ $t('settings.importexport.dbExportDesc') }}</p>
        <button @click="doExportDb" class="btn-secondary">{{ $t('settings.importexport.dbExportButton') }}</button>
      </div>

      <!-- Datenbank wiederherstellen (upload) -->
      <div class="card p-5 flex flex-col gap-3">
        <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.importexport.dbImportTitle') }}</h3>
        <p class="text-sm text-slate-400">{{ $t('settings.importexport.dbImportDesc') }}</p>
        <div class="p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg text-sm text-amber-600 dark:text-amber-400 flex flex-col gap-1">
          <p class="font-semibold">{{ $t('settings.importexport.dbImportWarning') }}</p>
          <ul class="list-disc list-inside text-xs mt-1 space-y-0.5">
            <li>{{ $t('settings.importexport.dbImportWarning1') }}</li>
            <li>{{ $t('settings.importexport.dbImportWarning2') }}</li>
            <li>{{ $t('settings.importexport.dbImportWarning3') }}</li>
            <li>{{ $t('settings.importexport.dbImportWarning4') }}</li>
          </ul>
        </div>
        <div class="flex items-center gap-3">
          <button type="button" class="btn-secondary btn-sm" @click="importDbFileInput.click()">{{ $t('common.chooseFile') }}</button>
          <span class="text-sm text-slate-400">{{ importDbFileName || $t('common.noFileSelected') }}</span>
          <input ref="importDbFileInput" type="file" accept=".sqlite,.db" @change="onImportDbFile" class="hidden" />
        </div>
        <div v-if="importDbResult" :class="['p-3 rounded-lg text-sm', importDbResult.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400']">{{ importDbResult.text }}</div>
      </div>

      <!-- Autobackup -->
      <div class="card p-5 flex flex-col gap-3">
        <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.importexport.autobackupTitle') }}</h3>
        <p class="text-sm text-slate-400">{{ $t('settings.importexport.autobackupDesc') }}</p>
        <div class="flex flex-col gap-3">
          <label class="flex items-center gap-2 cursor-pointer select-none">
            <input type="checkbox" v-model="autobackupCfg.enabled" @change="saveAutobackupConfig" class="w-4 h-4 rounded accent-blue-500" />
            <span class="text-sm text-slate-600 dark:text-slate-300">{{ $t('settings.importexport.autobackupEnable') }}</span>
          </label>
          <div v-if="autobackupCfg.enabled" class="flex flex-col gap-2 pl-6 border-l-2 border-blue-500/30">
            <div class="form-group">
              <label class="label">{{ $t('settings.importexport.autobackupTime') }}</label>
              <select v-model.number="autobackupCfg.hour" @change="saveAutobackupConfig" class="input text-sm">
                <option v-for="h in 24" :key="h-1" :value="h-1">{{ String(h-1).padStart(2,'0') }}:00 {{ $t('settings.importexport.autobackupUhr') }}</option>
              </select>
            </div>
            <div class="form-group">
              <label class="label">{{ $t('settings.importexport.autobackupRetention') }}</label>
              <select v-model.number="autobackupCfg.retention_days" @change="saveAutobackupConfig" class="input text-sm">
                <option v-for="d in 30" :key="d" :value="d">{{ d }} {{ d === 1 ? $t('settings.importexport.retentionDay') : $t('settings.importexport.retentionDays') }}</option>
              </select>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <button @click="runAutobackupNow" :disabled="autobackupRunning" class="btn-secondary btn-sm">
              <Spinner v-if="autobackupRunning" size="sm" />
              {{ $t('settings.importexport.autobackupNow') }}
            </button>
          </div>
          <div v-if="autobackupMsg" :class="['p-3 rounded-lg text-sm', autobackupMsg.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400']">{{ autobackupMsg.text }}</div>
        </div>
      </div>

      <!-- Autobackup wiederherstellen -->
      <div class="card p-5 flex flex-col gap-3">
        <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.importexport.autobackupRestoreTitle') }}</h3>
        <p class="text-sm text-slate-400">{{ $t('settings.importexport.autobackupRestoreDesc') }}</p>
        <div v-if="autobackupList.length === 0" class="text-sm text-slate-500 italic">{{ $t('settings.importexport.autobackupNone') }}</div>
        <div v-else class="flex flex-col gap-2">
          <div class="p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg text-sm text-amber-600 dark:text-amber-400 flex flex-col gap-1">
            <p class="font-semibold">{{ $t('settings.importexport.autobackupRestoreWarning') }}</p>
            <ul class="list-disc list-inside text-xs mt-1 space-y-0.5">
              <li>{{ $t('settings.importexport.autobackupRestoreWarning1') }}</li>
              <li>{{ $t('settings.importexport.autobackupRestoreWarning2') }}</li>
              <li>{{ $t('settings.importexport.autobackupRestoreWarning3') }}</li>
            </ul>
          </div>
          <div class="form-group">
            <label class="label">{{ $t('settings.importexport.autobackupSelect') }}</label>
            <select v-model="selectedAutobackup" class="input text-sm">
              <option value="">{{ $t('common.pleaseSelect') }}</option>
              <option v-for="entry in autobackupList" :key="entry.name" :value="entry.name">
                {{ formatAutobackupName(entry.name) }} ({{ formatBytes(entry.size_bytes) }})
              </option>
            </select>
          </div>
          <div class="flex items-center gap-2">
            <button @click="restoreAutobackup" :disabled="!selectedAutobackup || autobackupRestoring" class="btn-primary btn-sm">
              <Spinner v-if="autobackupRestoring" size="sm" color="white" />
              {{ $t('settings.importexport.autobackupRestore') }}
            </button>
          </div>
        </div>
        <div v-if="autobackupRestoreMsg" :class="['p-3 rounded-lg text-sm', autobackupRestoreMsg.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400']">{{ autobackupRestoreMsg.text }}</div>
      </div>

      <!-- KNX Projekt Import -->
      <div class="card p-5 flex flex-col gap-3">
        <div class="flex items-center gap-2">
          <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.importexport.knxTitle') }}</h3>
          <span class="text-xs text-slate-500 bg-slate-700/50 px-2 py-0.5 rounded">.knxproj</span>
        </div>
        <p class="text-sm text-slate-400">{{ $t('settings.importexport.knxDesc') }}</p>
        <div class="flex flex-col gap-2">
          <div class="flex items-center gap-3">
            <button type="button" class="btn-secondary btn-sm" @click="$refs.knxFileInput.click()">{{ $t('common.chooseFile') }}</button>
            <span class="text-sm text-slate-400">{{ knxFile?.name || $t('common.noFileSelected') }}</span>
            <input ref="knxFileInput" type="file" accept=".knxproj" @change="onKnxprojFile" class="hidden" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('settings.importexport.knxPassword') }} <span class="text-slate-600 font-normal">{{ $t('common.optional') }}</span></label>
            <input v-model="knxPassword" type="password" class="input text-sm" :placeholder="$t('settings.importexport.knxPasswordPlaceholder')" autocomplete="off" />
          </div>

          <!-- DataPoints anlegen -->
          <label class="flex items-center gap-2 cursor-pointer select-none mt-1">
            <input type="checkbox" v-model="knxCreateDps" class="w-4 h-4 rounded accent-blue-500" />
            <span class="text-sm text-slate-600 dark:text-slate-300">{{ $t('settings.importexport.knxCreateDps') }}</span>
          </label>

          <div v-if="knxCreateDps" class="flex flex-col gap-2 pl-6 border-l-2 border-blue-500/30">
            <div class="form-group">
              <label class="label">{{ $t('settings.importexport.knxAdapterInstance') }}</label>
              <select v-model="knxAdapterName" class="input text-sm">
                <option value="">{{ $t('common.pleaseSelect') }}</option>
                <option v-for="inst in knxAdapterInstances" :key="inst.name" :value="inst.name">{{ inst.name }}</option>
              </select>
              <p v-if="knxAdapterInstances.length === 0" class="text-xs text-amber-400 mt-1">
                {{ $t('settings.importexport.knxNoAdapter') }}
              </p>
            </div>
            <div class="form-group">
              <label class="label">{{ $t('settings.importexport.knxDirection') }}</label>
              <select v-model="knxDirection" class="input text-sm">
                <option value="BOTH">{{ $t('settings.importexport.knxDirectionBoth') }}</option>
                <option value="SOURCE">{{ $t('settings.importexport.knxDirectionSource') }}</option>
                <option value="DEST">{{ $t('settings.importexport.knxDirectionDest') }}</option>
              </select>
            </div>
          </div>

          <div class="flex items-center gap-3">
            <button @click="doKnxImport" class="btn-primary btn-sm"
              :disabled="!knxFile || knxImporting || (knxCreateDps && !knxAdapterName)">
              <Spinner v-if="knxImporting" size="sm" color="white" />
              {{ $t('settings.importexport.knxImport') }}
            </button>
          </div>
        </div>
        <div v-if="knxResult" :class="['p-3 rounded-lg text-sm', knxResult.ok ? 'bg-green-500/10 text-green-400 border border-green-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30']">
          {{ knxResult.text }}
        </div>
      </div>
    </div>

    <!-- ── History Backend ── -->
    <div v-if="activeTab === 'history'" class="flex flex-col gap-4 max-w-2xl" :class="{ 'pointer-events-none select-none opacity-60': isDemo }">
      <div class="card">
        <div class="card-header">
          <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.history.dbTitle') }}</h3>
        </div>
        <div class="card-body flex flex-col gap-4">
          <p class="text-sm text-slate-500">{{ $t('settings.history.dbDesc') }}</p>

          <!-- Plugin selector -->
          <div class="form-group">
            <label class="label">{{ $t('settings.history.dbLabel') }}</label>
            <select v-model="histForm.plugin" class="input text-sm">
              <option value="sqlite">{{ $t('settings.history.sqlite') }}</option>
              <option value="influxdb">{{ $t('settings.history.influxdb') }}</option>
              <option value="timescaledb">{{ $t('settings.history.timescaledb') }}</option>
            </select>
          </div>

          <div class="form-group">
            <label class="label">{{ $t('settings.history.defaultWindowHoursLabel') }}</label>
            <input
              v-model.number="histForm.default_window_hours"
              type="number"
              min="1"
              max="8760"
              class="input text-sm"
            />
            <p class="text-xs text-slate-500 mt-1">{{ $t('settings.history.defaultWindowHoursHint') }}</p>
          </div>

          <!-- InfluxDB settings -->
          <template v-if="histForm.plugin === 'influxdb'">
            <div class="form-group">
              <label class="label">{{ $t('settings.history.version') }}</label>
              <select v-model.number="histForm.influx_version" class="input text-sm">
                <option :value="1">{{ $t('settings.history.influxV1') }}</option>
                <option :value="2">{{ $t('settings.history.influxV2') }}</option>
                <option :value="3">{{ $t('settings.history.influxV3') }}</option>
              </select>
            </div>
            <div class="form-group">
              <label class="label">{{ $t('settings.history.url') }}</label>
              <input v-model="histForm.influx_url" type="text" class="input text-sm font-mono"
                placeholder="http://localhost:8086" />
            </div>

            <!-- v1: username + password + database -->
            <template v-if="histForm.influx_version === 1">
              <div class="grid grid-cols-2 gap-3">
                <div class="form-group">
                  <label class="label">{{ $t('settings.history.username') }}</label>
                  <input v-model="histForm.influx_username" type="text" class="input text-sm" autocomplete="off" />
                </div>
                <div class="form-group">
                  <label class="label">{{ $t('settings.history.password') }}</label>
                  <input v-model="histForm.influx_password" type="password" class="input text-sm" autocomplete="new-password" />
                </div>
              </div>
              <div class="form-group">
                <label class="label">{{ $t('settings.history.database') }}</label>
                <input v-model="histForm.influx_database" type="text" class="input text-sm font-mono" placeholder="obs" />
              </div>
            </template>

            <!-- v2: token + org + bucket -->
            <template v-if="histForm.influx_version === 2">
              <div class="form-group">
                <label class="label">{{ $t('settings.history.apiToken') }}</label>
                <input v-model="histForm.influx_token" type="password" class="input text-sm font-mono" autocomplete="new-password" />
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div class="form-group">
                  <label class="label">{{ $t('settings.history.organization') }}</label>
                  <input v-model="histForm.influx_org" type="text" class="input text-sm font-mono" placeholder="my-org" />
                </div>
                <div class="form-group">
                  <label class="label">{{ $t('settings.history.bucket') }}</label>
                  <input v-model="histForm.influx_bucket" type="text" class="input text-sm font-mono" placeholder="obs" />
                </div>
              </div>
            </template>

            <!-- v3: token + database -->
            <template v-if="histForm.influx_version === 3">
              <div class="form-group">
                <label class="label">{{ $t('settings.history.apiToken') }}</label>
                <input v-model="histForm.influx_token" type="password" class="input text-sm font-mono" autocomplete="new-password" />
              </div>
              <div class="form-group">
                <label class="label">{{ $t('settings.history.database') }}</label>
                <input v-model="histForm.influx_database" type="text" class="input text-sm font-mono" placeholder="obs" />
              </div>
            </template>
          </template>

          <!-- TimescaleDB settings -->
          <template v-if="histForm.plugin === 'timescaledb'">
            <div class="form-group">
              <label class="label">{{ $t('settings.history.connectionDsn') }}</label>
              <input v-model="histForm.timescale_dsn" type="text" class="input text-sm font-mono"
                :placeholder="$t('settings.history.connectionDsnPlaceholder')" autocomplete="off" />

            </div>
          </template>

          <!-- Test + feedback -->
          <div v-if="histTestResult" :class="['p-3 rounded-lg text-sm border', histTestResult.ok ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30']">
            {{ histTestResult.message }}
          </div>
          <div v-if="histSaveMsg" :class="['p-3 rounded-lg text-sm border', histSaveMsg.ok ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30']">
            {{ histSaveMsg.text }}
          </div>

          <div class="flex items-center gap-3">
            <button @click="testHistoryConnection" class="btn-secondary" :disabled="histTesting">
              <Spinner v-if="histTesting" size="sm" />
              {{ $t('settings.history.testButton') }}
            </button>
            <button @click="saveHistorySettings" class="btn-primary" :disabled="histSaving">
              <Spinner v-if="histSaving" size="sm" color="white" />
              {{ $t('settings.history.saveButton') }}
            </button>
          </div>
        </div>
      </div>

      <!-- Objekt-Filter -->
      <div class="card" data-testid="history-filter-card">
        <div class="card-header">
          <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.history.filterTitle') }}</h3>
          <span class="text-xs text-slate-500">{{ $t('settings.history.filterCount', { excluded: histFilterExcludedCount, total: histAllDps.length }) }}</span>
        </div>
        <div class="card-body flex flex-col gap-3">
          <p class="text-sm text-slate-500">{{ $t('settings.history.filterDesc') }}</p>

          <!-- Search -->
          <input
            v-model="histFilterSearch"
            type="text"
            class="input text-sm"
            :placeholder="$t('settings.history.filterSearch')"
            data-testid="input-history-filter-search"
          />

          <!-- Loading -->
          <div v-if="histFilterLoading" class="flex justify-center py-4" data-testid="history-filter-loading"><Spinner /></div>

          <!-- Empty state -->
          <div v-else-if="histFilteredDps.length === 0" class="text-sm text-slate-500 text-center py-4" data-testid="history-filter-empty">
            {{ $t('settings.history.filterEmpty') }}
          </div>

          <!-- DataPoint list -->
          <div v-else class="flex flex-col divide-y divide-slate-200 dark:divide-slate-700/60 max-h-96 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700/60" data-testid="history-filter-list">
            <div
              v-for="dp in histFilteredDps"
              :key="dp.id"
              class="flex items-center gap-3 px-3 py-2 hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors"
              :data-testid="`history-filter-row-${dp.id}`"
            >
              <div class="flex-1 min-w-0">
                <div class="text-sm font-medium text-slate-700 dark:text-slate-200 truncate">{{ dp.name }}</div>
                <div class="text-xs text-slate-500 font-mono truncate">{{ dp.data_type }}{{ dp.unit ? ' · ' + dp.unit : '' }}</div>
              </div>
              <button
                @click="toggleHistoryFilter(dp)"
                :class="[
                  'shrink-0 relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none',
                  dp.record_history ? 'bg-green-500' : 'bg-slate-300 dark:bg-slate-600'
                ]"
                :title="dp.record_history ? $t('settings.history.filterEnableTitle') : $t('settings.history.filterDisableTitle')"
                :data-testid="`toggle-history-${dp.id}`"
              >
                <span
                  :class="[
                    'inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform',
                    dp.record_history ? 'translate-x-4' : 'translate-x-0.5'
                  ]"
                />
              </button>
            </div>
          </div>

          <!-- Schnellauswahl -->
          <div class="flex items-center gap-2 pt-1">
            <button @click="histFilterSetAll(true)" class="btn-secondary btn-sm" data-testid="btn-history-filter-enable-all">{{ $t('settings.history.filterEnableAll') }}</button>
            <button @click="histFilterSetAll(false)" class="btn-secondary btn-sm" data-testid="btn-history-filter-disable-all">{{ $t('settings.history.filterDisableAll') }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ── Hierarchie ── -->
    <div v-if="activeTab === 'hierarchy'" class="flex flex-col gap-4" data-testid="hierarchy-tab">
      <div class="card">
        <div class="card-body" :class="{ 'pointer-events-none select-none opacity-60': isDemo }">
          <HierarchyManager />
        </div>
      </div>
    </div>

    <!-- ── Icons Library ── -->
    <div v-if="activeTab === 'icons'" class="flex flex-col gap-4" data-testid="icons-tab" :class="{ 'pointer-events-none select-none opacity-60': isDemo }">

      <!-- Toolbar -->
      <div class="flex flex-wrap items-center gap-3">
        <span class="text-sm text-slate-400" data-testid="icons-count">{{ $t('settings.icons.count', { n: iconsFiltered.length }) }}</span>
        <div class="flex-1" />
        <button v-if="iconsSelected.size > 0" @click="doIconsExport" class="btn-secondary btn-sm" data-testid="btn-icons-export">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3"/></svg>
          {{ $t('settings.icons.export', { n: iconsSelected.size }) }}
        </button>
        <button v-if="iconsSelected.size > 0" @click="doIconsDelete" class="btn-danger btn-sm" data-testid="btn-icons-delete">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7h6m-7 0a1 1 0 011-1h4a1 1 0 011 1m-7 0h8"/></svg>
          {{ $t('settings.icons.delete', { n: iconsSelected.size }) }}
        </button>
        <button v-if="icons.length > 0" @click="iconsSelectAll" class="btn-secondary btn-sm" data-testid="btn-icons-select-all">
          {{ iconsSelected.size === icons.length ? $t('settings.icons.deselectAll') : $t('settings.icons.selectAll') }}
        </button>
        <input v-model="iconsSearch" type="text" class="input text-sm w-40" :placeholder="$t('settings.icons.search')" data-testid="input-icons-search" />
      </div>

      <!-- Feedback -->
      <div v-if="iconsMsg" :class="['p-3 rounded-lg text-sm border', iconsMsg.ok ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30']"
        data-testid="icons-msg">
        {{ iconsMsg.text }}
      </div>

      <!-- Icon Grid -->
      <div v-if="iconsLoading" class="flex justify-center py-10"><Spinner /></div>
      <div v-else-if="icons.length === 0" class="text-center text-sm text-slate-500 py-10" data-testid="icons-empty">
        {{ $t('settings.icons.empty') }}
      </div>
      <div v-else class="grid grid-cols-[repeat(auto-fill,minmax(100px,1fr))] gap-3" data-testid="icons-grid">
        <label v-for="icon in iconsFiltered" :key="icon.name" :title="icon.name"
          :data-testid="`icon-item-${icon.name}`"
          :class="['relative flex flex-col items-center gap-1.5 p-3 rounded-lg border cursor-pointer transition-colors select-none',
            iconsSelected.has(icon.name)
              ? 'border-blue-500 bg-blue-50 dark:bg-blue-500/10'
              : 'border-slate-200 dark:border-slate-700/50 hover:bg-slate-50 dark:hover:bg-slate-800/40']">
          <input type="checkbox" class="sr-only" :value="icon.name"
            :checked="iconsSelected.has(icon.name)"
            @change="iconsToggle(icon.name)" />
          <!-- SVG rendered in black, fill overridden via CSS -->
          <div class="w-10 h-10 flex items-center justify-center [&_svg]:w-full [&_svg]:h-full brightness-0 dark:invert"
            v-html="icon.content" />
          <span class="text-xs text-slate-500 dark:text-slate-400 truncate w-full text-center">{{ icon.name }}</span>
          <div v-if="iconsSelected.has(icon.name)"
            class="absolute top-1.5 right-1.5 w-4 h-4 rounded-full bg-blue-500 flex items-center justify-center">
            <svg class="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/>
            </svg>
          </div>
        </label>
      </div>

      <!-- Upload area -->
      <div class="card">
        <div class="card-header"><h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.icons.importTitle') }}</h3></div>
        <div class="card-body flex flex-col gap-4">
          <p class="text-sm text-slate-400">{{ $t('settings.icons.importDesc') }}</p>

          <!-- Drag & Drop Zone -->
          <div
            class="relative border-2 border-dashed rounded-lg transition-colors cursor-pointer"
            :class="iconsDragOver
              ? 'border-blue-500 bg-blue-500/5'
              : 'border-slate-300 dark:border-slate-600 hover:border-slate-400 dark:hover:border-slate-500'"
            @dragover.prevent="iconsDragOver = true"
            @dragleave.prevent="iconsDragOver = false"
            @drop.prevent="onIconsDrop"
            @click="$refs.iconsFileInput.click()"
            data-testid="icons-dropzone">
            <div class="flex flex-col items-center gap-2 py-8 px-4 text-center pointer-events-none">
              <svg class="w-8 h-8 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
              </svg>
              <div>
                <p class="text-sm text-slate-600 dark:text-slate-300 font-medium">{{ $t('settings.icons.dropzone') }}</p>
                <p class="text-xs text-slate-400 mt-0.5">{{ $t('settings.icons.dropzoneClick') }}</p>
              </div>
            </div>
            <input ref="iconsFileInput" type="file" accept=".svg,.zip" multiple class="sr-only"
              data-testid="input-icons-file"
              @change="onIconsFileSelect" />
          </div>

          <div v-if="iconsUploading" class="flex items-center gap-2 text-sm text-slate-500">
            <Spinner size="sm" />
            {{ $t('settings.icons.uploading') }}
          </div>
        </div>
      </div>

      <!-- KNX UF Iconset Import -->
      <div class="card">
        <div class="card-header">
          <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.icons.knxufTitle') }}</h3>
        </div>
        <div class="card-body flex flex-col gap-4">
          <p class="text-sm text-slate-400">{{ $t('settings.icons.knxufDesc') }}</p>
          <div v-if="knxufMsg" :class="['p-3 rounded-lg text-sm border', knxufMsg.ok ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30']">
            {{ knxufMsg.text }}
          </div>
          <button @click="doKnxufImport" class="btn-primary btn-sm w-fit" :disabled="knxufImporting" data-testid="btn-knxuf-import">
            <Spinner v-if="knxufImporting" size="sm" color="white" />
            {{ knxufImporting ? $t('settings.icons.knxufImporting') : $t('settings.icons.knxufButton') }}
          </button>
        </div>
      </div>

      <!-- FontAwesome Import -->
      <div class="card">
        <div class="card-header">
          <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.icons.faTitle') }}</h3>
        </div>
        <div class="card-body flex flex-col gap-4">
          <p class="text-sm text-slate-400">{{ $t('settings.icons.faDesc') }}</p>
          <!-- API Key speichern -->
          <div class="form-group">
            <label class="label flex items-center gap-2">
              {{ $t('settings.icons.faApiKeyLabel') }}
              <span v-if="faSavedKey" class="text-xs font-normal px-1.5 py-0.5 rounded bg-green-500/15 text-green-400 border border-green-500/30">{{ $t('settings.icons.faApiKeySaved') }}</span>
            </label>
            <div class="flex gap-2">
              <input v-model="faApiKey" type="password" class="input text-sm font-mono flex-1"
                :placeholder="faSavedKey ? '••••••••••••••••••••••••••••••••••••' : $t('settings.icons.faApiKeyPlaceholder')"
                autocomplete="new-password" data-testid="input-fa-apikey" />
              <button @click="doSaveFaKey" class="btn-secondary btn-sm whitespace-nowrap"
                :disabled="!faApiKey.trim()" :title="$t('settings.apikeys.save')">
                {{ $t('common.save') }}
              </button>
              <button v-if="faSavedKey" @click="doDeleteFaKey" class="btn-danger btn-sm whitespace-nowrap"
                :title="$t('settings.apikeys.delete')">
                {{ $t('common.delete') }}
              </button>
            </div>
            <p class="text-xs text-slate-500 mt-1">{{ $t('settings.icons.faApiKeyHint') }}</p>
          </div>

          <!-- Icon-Namen + Stil -->
          <div class="form-group">
            <label class="label">{{ $t('settings.icons.faNamesLabel') }}</label>
            <input v-model="faIconNames" type="text" class="input text-sm font-mono"
              :placeholder="$t('settings.icons.faNamesPlaceholder')" data-testid="input-fa-names" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('settings.icons.faStyleLabel') }}</label>
            <select v-model="faStyle" class="input text-sm" data-testid="select-fa-style">
              <option value="solid">Solid</option>
              <option value="regular">Regular</option>
              <option value="brands">Brands</option>
              <template v-if="faSavedKey || faApiKey.trim()">
                <option disabled class="text-slate-500">{{ $t('settings.icons.faStylePro') }}</option>
                <option value="light">Light</option>
                <option value="thin">Thin</option>
                <option value="duotone">Duotone</option>
              </template>
            </select>
          </div>

          <div v-if="faMsg" :class="['p-3 rounded-lg text-sm border', faMsg.ok ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30']">
            {{ faMsg.text }}
            <!-- Debug-Panel: bei Bedarf wieder aktivieren (debug=dbg im Backend einschalten)
            <pre v-if="faMsg.debug?.length" class="mt-2 text-xs opacity-80 whitespace-pre-wrap font-mono bg-black/20 rounded p-2 max-h-64 overflow-auto">{{ faMsg.debug.join('\n') }}</pre>
            -->
          </div>
          <button @click="doFaImport" class="btn-primary btn-sm w-fit" :disabled="faImporting || !faIconNames.trim()" data-testid="btn-fa-import">
            <Spinner v-if="faImporting" size="sm" color="white" />
            {{ $t('settings.icons.faImport') }}
          </button>
        </div>
      </div>
    </div>

    <!-- ── Links ── -->
    <div v-if="activeTab === 'links'" class="flex flex-col gap-4 max-w-lg" data-testid="links-tab" :class="{ 'pointer-events-none select-none opacity-60': isDemo }">
      <div class="card">
        <div class="card-header flex items-center justify-between">
          <div>
            <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.links.title') }}</h3>
            <p class="text-xs text-slate-500 mt-0.5">{{ $t('settings.links.desc') }}</p>
          </div>
          <button @click="openNavLinkForm()" class="btn-primary btn-sm" data-testid="btn-add-nav-link">
            {{ $t('settings.links.addButton') }}
          </button>
        </div>
        <div class="card-body flex flex-col gap-2">

          <!-- Leer-Zustand -->
          <div v-if="!navStore.loading && navStore.links.length === 0" class="text-sm text-slate-500 py-4 text-center" data-testid="nav-links-empty">
            {{ $t('settings.links.empty') }}
          </div>

          <!-- Link-Liste -->
          <div v-for="link in navStore.links" :key="link.id"
            class="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-slate-200 dark:border-slate-700/60 bg-slate-50 dark:bg-slate-800/40"
            :data-testid="'nav-link-row-' + link.id">
            <span class="text-lg w-6 text-center shrink-0"><VisuIcon :icon="link.icon || '🔗'" /></span>
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium text-slate-800 dark:text-slate-100 truncate">{{ link.label }}</div>
              <div class="text-xs text-slate-500 truncate">{{ link.url }}</div>
            </div>
            <span v-if="link.open_new_tab" class="text-xs px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 text-slate-500 shrink-0">{{ $t('settings.links.newTab') }}</span>
            <button @click="openNavLinkForm(link)" class="btn-ghost btn-sm text-slate-400 hover:text-blue-500 shrink-0" :title="$t('settings.links.editButtonTitle')">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536M9 13l6.586-6.586a2 2 0 012.828 2.828L11.828 15.828a2 2 0 01-2.828 0L7 13.657 9 13zm-4 8h4l9-9-4-4-9 9v4z"/></svg>
            </button>
            <button @click="deleteNavLink(link.id)" class="btn-ghost btn-sm text-slate-400 hover:text-red-500 shrink-0" :title="$t('settings.links.deleteButtonTitle')" :data-testid="'btn-delete-nav-link-' + link.id">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7V4h6v3M4 7h16"/></svg>
            </button>
          </div>

          <div v-if="navLinksMsg" :class="['p-3 rounded-lg text-sm border', navLinksMsg.ok ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30']">
            {{ navLinksMsg.text }}
          </div>
        </div>
      </div>

      <!-- Link-Formular -->
      <div v-if="navLinkShowForm" class="card" data-testid="nav-link-form">
        <div class="card-header">
          <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ navLinkEditId ? $t('settings.links.editTitle') : $t('settings.links.createTitle') }}</h3>
        </div>
        <div class="card-body flex flex-col gap-4">
          <div class="form-group">
            <label class="label">{{ $t('settings.links.label') }} <span class="text-red-400">*</span></label>
            <input v-model="navLinkForm.label" type="text" class="input" :placeholder="$t('settings.links.labelPlaceholder')" data-testid="input-nav-link-label" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('settings.links.urlLabel') }} <span class="text-red-400">*</span></label>
            <input v-model="navLinkForm.url" type="url" class="input" :placeholder="$t('settings.links.urlPlaceholder')" data-testid="input-nav-link-url" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('settings.links.icon') }}</label>
            <div class="p-3 rounded-lg border border-slate-200 dark:border-slate-700/60 bg-slate-50 dark:bg-slate-800/40">
              <IconPicker v-model="navLinkForm.icon" data-testid="input-nav-link-icon" />
            </div>
          </div>
          <div class="form-group">
            <label class="label">{{ $t('settings.links.order') }}</label>
            <input v-model.number="navLinkForm.sort_order" type="number" min="0" class="input w-24" data-testid="input-nav-link-order" />
          </div>
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" v-model="navLinkForm.open_new_tab" class="w-4 h-4 rounded accent-blue-500" data-testid="check-nav-link-new-tab" />
            <span class="text-sm text-slate-700 dark:text-slate-300">{{ $t('settings.links.openNewTab') }}</span>
          </label>
          <div v-if="navLinksMsg" :class="['p-3 rounded-lg text-sm border', navLinksMsg.ok ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30']">
            {{ navLinksMsg.text }}
          </div>
          <div class="flex justify-end gap-3">
            <button type="button" @click="cancelNavLinkForm" class="btn-secondary" data-testid="btn-cancel-nav-link">{{ $t('common.cancel') }}</button>
            <button type="button" @click="saveNavLink" class="btn-primary" :disabled="navLinksSaving" data-testid="btn-save-nav-link">
              <Spinner v-if="navLinksSaving" size="sm" color="white" />
              {{ $t('common.save') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- ── Danger Zone ── -->
    <div v-if="activeTab === 'dangerzone'" class="flex flex-col gap-4 max-w-lg" :class="{ 'pointer-events-none select-none opacity-60': isDemo }">
      <div class="rounded-lg border border-red-500/40 bg-red-500/5 overflow-hidden">
        <div class="px-5 py-3 border-b border-red-500/30 flex items-center gap-2">
          <svg class="w-4 h-4 text-red-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
          </svg>
          <h3 class="font-semibold text-sm text-red-400">{{ $t('settings.dangerzone.title') }}</h3>
        </div>
        <div class="divide-y divide-red-500/20">

          <!-- Verknüpfungen -->
          <div class="p-5 flex items-start justify-between gap-4">
            <div>
              <p class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ $t('settings.dangerzone.bindings.label') }}</p>
              <p class="text-xs text-slate-500 mt-1">{{ $t('settings.dangerzone.bindings.desc') }}</p>
            </div>
            <button @click="showConfirm('bindings')" class="btn-danger btn-sm shrink-0">{{ $t('common.delete') }}</button>
          </div>

          <!-- DataPoints -->
          <div class="p-5 flex items-start justify-between gap-4">
            <div>
              <p class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ $t('settings.dangerzone.datapoints.label') }}</p>
              <p class="text-xs text-slate-500 mt-1">{{ $t('settings.dangerzone.datapoints.desc') }}</p>
            </div>
            <button @click="showConfirm('datapoints')" class="btn-danger btn-sm shrink-0">{{ $t('common.delete') }}</button>
          </div>

          <!-- Logic -->
          <div class="p-5 flex items-start justify-between gap-4">
            <div>
              <p class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ $t('settings.dangerzone.logic.label') }}</p>
              <p class="text-xs text-slate-500 mt-1">{{ $t('settings.dangerzone.logic.desc') }}</p>
            </div>
            <button @click="showConfirm('logic')" class="btn-danger btn-sm shrink-0">{{ $t('common.delete') }}</button>
          </div>

          <!-- Adapters -->
          <div class="p-5 flex items-start justify-between gap-4">
            <div>
              <p class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ $t('settings.dangerzone.adapters.label') }}</p>
              <p class="text-xs text-slate-500 mt-1">{{ $t('settings.dangerzone.adapters.desc') }}</p>
            </div>
            <button @click="showConfirm('adapters')" class="btn-danger btn-sm shrink-0">{{ $t('common.delete') }}</button>
          </div>

          <!-- KNX Group Addresses -->
          <div class="p-5 flex items-start justify-between gap-4">
            <div>
              <p class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ $t('settings.dangerzone.knxga.label') }}</p>
              <p class="text-xs text-slate-500 mt-1">{{ $t('settings.dangerzone.knxga.desc', { n: knxGaCount }) }}</p>
            </div>
            <button @click="showConfirm('knxga')" :disabled="knxGaCount === 0" class="btn-danger btn-sm shrink-0">{{ $t('common.delete') }}</button>
          </div>

          <!-- Factory Reset -->
          <div class="p-5 flex items-start justify-between gap-4">
            <div>
              <p class="text-sm font-medium text-slate-700 dark:text-slate-200">{{ $t('settings.dangerzone.factory.label') }}</p>
              <p class="text-xs text-slate-500 mt-1">{{ $t('settings.dangerzone.factory.desc') }}</p>
            </div>
            <button @click="showConfirm('all')" class="btn-danger btn-sm shrink-0">{{ $t('settings.dangerzone.factory.confirmLabel') }}</button>
          </div>

          <!-- Feedback -->
          <div v-if="resetResult" class="px-5 py-3">
            <div :class="['p-3 rounded-lg text-sm', resetResult.ok ? 'bg-green-500/10 text-green-400 border border-green-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30']">
              {{ resetResult.text }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Modals -->
    <Modal v-model="showCreateUser" :title="$t('settings.users.createTitle')" max-width="sm">
      <form @submit.prevent="doCreateUser" class="flex flex-col gap-4">
        <div class="form-group">
          <label class="label">{{ $t('settings.users.colUsername') }}</label>
          <input v-model="userForm.username" type="text" class="input" required />
        </div>
        <div class="form-group">
          <label class="label">{{ $t('login.password') }}</label>
          <input v-model="userForm.password" type="password" class="input" required autocomplete="new-password" />
        </div>
        <div class="flex items-center gap-2">
          <input type="checkbox" id="isAdmin" v-model="userForm.is_admin" class="w-4 h-4 rounded" />
          <label for="isAdmin" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('settings.users.isAdmin') }}</label>
        </div>
        <div class="flex items-center gap-2">
          <input type="checkbox" id="mqttEnabled" v-model="userForm.mqtt_enabled" class="w-4 h-4 rounded" />
          <label for="mqttEnabled" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('settings.users.mqttEnable') }}</label>
        </div>
        <div v-if="userForm.mqtt_enabled" class="form-group">
          <label class="label">{{ $t('settings.users.mqttPassword') }}</label>
          <input v-model="userForm.mqtt_password" type="password" class="input" autocomplete="new-password" :placeholder="$t('common.noMqttPassword')" />
        </div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showCreateUser = false" class="btn-secondary">{{ $t('common.cancel') }}</button>
          <button type="submit" class="btn-primary">{{ $t('settings.users.create') }}</button>
        </div>
      </form>
    </Modal>

    <Modal v-model="showMqttPassword" :title="$t('settings.users.mqttPasswordTitle')" max-width="sm">
      <form @submit.prevent="doSetMqttPassword" class="flex flex-col gap-4">
        <p class="text-sm text-slate-400">{{ $t('settings.users.mqttFor', { username: mqttTarget?.username }) }}</p>
        <div class="form-group">
          <label class="label">{{ $t('settings.users.newMqttPassword') }}</label>
          <input v-model="mqttPasswordInput" type="password" class="input" required autocomplete="new-password" />
        </div>
        <div v-if="mqttMsg" :class="['p-3 rounded-lg text-sm', mqttMsg.ok ? 'bg-green-500/10 text-green-400 border border-green-500/30' : 'bg-red-500/10 text-red-400 border border-red-500/30']">{{ mqttMsg.text }}</div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showMqttPassword = false" class="btn-secondary">{{ $t('common.cancel') }}</button>
          <button type="submit" class="btn-primary" :disabled="mqttSaving">
            <Spinner v-if="mqttSaving" size="sm" color="white" />
            {{ $t('common.save') }}
          </button>
        </div>
      </form>
    </Modal>

    <Modal v-model="showNewKeyName" :title="$t('settings.apikeys.modalTitle')" max-width="sm">
      <form @submit.prevent="doCreateKey" class="flex flex-col gap-4">
        <div class="form-group">
          <label class="label">{{ $t('settings.apikeys.nameLabel') }}</label>
          <input v-model="newKeyName" type="text" class="input" :placeholder="$t('settings.apikeys.namePlaceholder')" required />
        </div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showNewKeyName = false" class="btn-secondary">{{ $t('common.cancel') }}</button>
          <button type="submit" class="btn-primary">{{ $t('settings.apikeys.create') }}</button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="showUserConfirm" :title="$t('settings.users.deleteUser')"
      :message="$t('settings.users.deleteUserConfirm', { name: deleteUserTarget?.username })"
      :confirm-label="$t('common.delete')" @confirm="doDeleteUser" />

    <ConfirmDialog v-model="showDzConfirm" :title="dzConfirmTitle"
      :message="dzConfirmMessage" :confirm-label="dzConfirmLabel" @confirm="doDzAction" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { authApi, adapterApi, configApi, autobackupApi, knxprojApi, historySettingsApi, iconsApi, dpApi } from '@/api/client'
import { useI18n } from 'vue-i18n'
import { useNavLinksStore } from '@/stores/navLinks'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import { useTz } from '@/composables/useTz'
import Badge            from '@/components/ui/Badge.vue'
import Spinner          from '@/components/ui/Spinner.vue'
import HierarchyManager from '@/components/HierarchyManager.vue'
import Modal          from '@/components/ui/Modal.vue'
import ConfirmDialog  from '@/components/ui/ConfirmDialog.vue'
import IconPicker     from '@/components/ui/IconPicker.vue'
import VisuIcon       from '@/components/ui/VisuIcon.vue'
import LocaleSwitcher from '@/components/ui/LocaleSwitcher.vue'

const { t, te } = useI18n()
const auth     = useAuthStore()
const settings = useSettingsStore()
const navStore = useNavLinksStore()
const { fmtDate } = useTz()
const activeTab = ref('general')
const isDemo   = computed(() => auth.username === 'demo')

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
  if (auth.isAdmin) {
    loadHistorySettings()
    loadHistoryFilterDps()
  }
})

watch(activeTab, (tab) => {
  if (tab === 'history') {
    loadHistorySettings()
    loadHistoryFilterDps()
  }
  if (tab === 'icons') { loadIcons(); loadFaSettings() }
  if (tab === 'links') { navStore.load() }
})

onUnmounted(() => {
  document.removeEventListener('mousedown', onOutsideClick)
})

async function saveTz() {
  tzSaving.value = true; tzMsg.value = null
  try {
    await settings.save(tzSelected.value)
    tzMsg.value = { ok: true, text: t('settings.general.tzSaved', { tz: tzSelected.value }) }
  } catch (e) {
    tzMsg.value = { ok: false, text: e.response?.data?.detail ?? t('common.saveError') }
  } finally {
    tzSaving.value = false
  }
}

const tabs = computed(() => [
  { id: 'general',      label: t('settings.tabs.general') },
  { id: 'password',     label: t('settings.tabs.password') },
  ...(auth.isAdmin || isDemo.value ? [{ id: 'users', label: t('settings.tabs.users') }] : []),
  { id: 'apikeys',      label: t('settings.tabs.apikeys') },
  { id: 'links',        label: t('settings.tabs.links') },
  { id: 'hierarchy',    label: t('settings.tabs.hierarchy') },
  { id: 'importexport', label: t('settings.tabs.importexport') },
  { id: 'icons',        label: t('settings.tabs.icons') },
  { id: 'history',      label: t('settings.tabs.history') },
  { id: 'dangerzone',   label: t('settings.tabs.dangerzone') },
])

// ── History Backend ────────────────────────────────────────────────────────
const histForm = reactive({
  plugin: 'sqlite',
  default_window_hours: 168,
  influx_url: 'http://localhost:8086',
  influx_version: 2,
  influx_token: '',
  influx_org: '',
  influx_bucket: 'obs',
  influx_database: 'obs',
  influx_username: '',
  influx_password: '',
  timescale_dsn: '',
})
const histSaving     = ref(false)
const histTesting    = ref(false)
const histSaveMsg    = ref(null)
const histTestResult = ref(null)

async function loadHistorySettings() {
  try {
    const { data } = await historySettingsApi.get()
    Object.assign(histForm, data)
  } catch (_) { /* non-critical */ }
}

function historySettingsPayload() {
  const hours = Number(histForm.default_window_hours)
  return {
    ...histForm,
    default_window_hours: Number.isFinite(hours) ? hours : 168,
  }
}

async function saveHistorySettings() {
  histSaving.value = true; histSaveMsg.value = null
  try {
    await historySettingsApi.update(historySettingsPayload())
    histSaveMsg.value = { ok: true, text: t('settings.history.saved') }
  } catch (e) {
    histSaveMsg.value = { ok: false, text: e.response?.data?.detail ?? t('common.saveError') }
  } finally {
    histSaving.value = false
  }
}

async function testHistoryConnection() {
  histTesting.value = true; histTestResult.value = null
  try {
    const { data } = await historySettingsApi.test(historySettingsPayload())
    histTestResult.value = data
  } catch (e) {
    histTestResult.value = { ok: false, message: e.response?.data?.detail ?? t('settings.history.testError') }
  } finally {
    histTesting.value = false
  }
}

// ── History Objekt-Filter ──────────────────────────────────────────────────
const histAllDps       = ref([])
const histFilterSearch = ref('')
const histFilterLoading = ref(false)

const histFilteredDps = computed(() => {
  const q = histFilterSearch.value.toLowerCase().trim()
  const filtered = q
    ? histAllDps.value.filter(dp =>
        dp.name.toLowerCase().includes(q) ||
        dp.id.toLowerCase().includes(q) ||
        dp.data_type.toLowerCase().includes(q) ||
        (dp.unit ?? '').toLowerCase().includes(q)
      )
    : histAllDps.value
  // Ausgeschlossene Objekte (record_history=false) zuerst
  return [...filtered].sort((a, b) => {
    if (a.record_history === b.record_history) return 0
    return a.record_history ? 1 : -1
  })
})

const histFilterExcludedCount = computed(() =>
  histAllDps.value.filter(dp => !dp.record_history).length
)

async function loadHistoryFilterDps() {
  histFilterLoading.value = true
  try {
    const { data } = await dpApi.listAll()
    histAllDps.value = data.items ?? []
  } catch { /* non-critical */ }
  finally { histFilterLoading.value = false }
}

async function toggleHistoryFilter(dp) {
  const newVal = !dp.record_history
  try {
    await dpApi.update(dp.id, { record_history: newVal })
    dp.record_history = newVal
  } catch (e) {
    console.error('Fehler beim Aktualisieren der Historisierung:', e)
  }
}

async function histFilterSetAll(enable) {
  const targets = histFilteredDps.value.filter(dp => dp.record_history !== enable)
  await Promise.all(targets.map(dp => toggleHistoryFilter(dp)))
}

// ── Nav Links ─────────────────────────────────────────────────────────────
const navLinksSaving  = ref(false)
const navLinksMsg     = ref(null)
const navLinkEditId   = ref(null)
const navLinkForm     = reactive({
  label: '',
  url: '',
  icon: '',
  sort_order: 0,
  open_new_tab: true,
})
const navLinkShowForm = ref(false)

function openNavLinkForm(link = null) {
  if (link) {
    navLinkEditId.value = link.id
    Object.assign(navLinkForm, { label: link.label, url: link.url, icon: link.icon, sort_order: link.sort_order, open_new_tab: link.open_new_tab })
  } else {
    navLinkEditId.value = null
    Object.assign(navLinkForm, {
      label: '',
      url: '',
      icon: '',
      sort_order: navStore.links.length,
      open_new_tab: true,
    })
  }
  navLinkShowForm.value = true
  navLinksMsg.value = null
}

function cancelNavLinkForm() {
  navLinkShowForm.value = false
  navLinkEditId.value = null
  navLinksMsg.value = null
}

async function saveNavLink() {
  if (!navLinkForm.label.trim() || !navLinkForm.url.trim()) {
    navLinksMsg.value = { ok: false, text: t('settings.links.requiredFields') }
    return
  }
  navLinksSaving.value = true; navLinksMsg.value = null
  try {
    if (navLinkEditId.value) {
      await navStore.update(navLinkEditId.value, { ...navLinkForm })
    } else {
      await navStore.create({ ...navLinkForm })
    }
    navLinkShowForm.value = false
    navLinkEditId.value = null
  } catch (e) {
    navLinksMsg.value = { ok: false, text: e.response?.data?.detail ?? t('common.saveError') }
  } finally {
    navLinksSaving.value = false
  }
}

async function deleteNavLink(id) {
  try {
    await navStore.remove(id)
  } catch (e) {
    navLinksMsg.value = { ok: false, text: e.response?.data?.detail ?? t('common.deleteError') }
  }
}

// ── Theme ──────────────────────────────────────────────────────────────────
const themeOptions = computed(() => [
  { value: 'system', label: t('settings.general.themeSystem'), desc: t('settings.general.themeSystemDesc') },
  { value: 'light',  label: t('settings.general.themeLight'),  desc: t('settings.general.themeLightDesc') },
  { value: 'dark',   label: t('settings.general.themeDark'),   desc: t('settings.general.themeDarkDesc') },
])
const selectedTheme = computed({
  get: () => settings.theme,
  set: (v) => settings.setTheme(v),
})

// ── Password ──────────────────────────────────────────────────────────────
const pwForm  = reactive({ current: '', new1: '', new2: '' })
const pwSaving = ref(false)
const pwMsg    = ref(null)

async function changePassword() {
  if (pwForm.new1 !== pwForm.new2) { pwMsg.value = { ok: false, text: t('settings.password.mismatch') }; return }
  pwSaving.value = true; pwMsg.value = null
  try {
    await authApi.changePassword(pwForm.current, pwForm.new1)
    pwMsg.value = { ok: true, text: t('settings.password.success') }
    pwForm.current = ''; pwForm.new1 = ''; pwForm.new2 = ''
  } catch (e) {
    pwMsg.value = { ok: false, text: e.response?.data?.detail ?? t('common.error') }
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
    mqttMsg.value = { ok: true, text: t('settings.users.mqttPasswordSet') }
    await loadUsers()
    setTimeout(() => { showMqttPassword.value = false }, 800)
  } catch (e) {
    mqttMsg.value = { ok: false, text: e.response?.data?.detail ?? t('common.error') }
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
const importResult    = ref(null)
const importDbResult  = ref(null)
const importFileName  = ref('')
const importDbFileName = ref('')
const importFileInput  = ref(null)
const importDbFileInput = ref(null)

function _ts() {
  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}`
}

async function doExport() {
  const { data } = await configApi.export()
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url  = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = `obs_Backup_${_ts()}.json`; a.click()
  URL.revokeObjectURL(url)
}

async function doExportDb() {
  const { data: blob } = await configApi.exportDb()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = `obs_DB_${_ts()}.sqlite`; a.click()
  URL.revokeObjectURL(url)
}

async function onImportFile(e) {
  const file = e.target.files[0]; if (!file) return
  importFileName.value = file.name
  const text = await file.text()
  try {
    const payload = JSON.parse(text)
    const { data } = await configApi.import(payload)
    const gaInfo    = data.knx_group_addresses_upserted > 0 ? t('settings.importexport.importResultKnx', { n: data.knx_group_addresses_upserted }) : ''
    const lgTotal   = (data.logic_graphs_created ?? 0) + (data.logic_graphs_updated ?? 0)
    const lgInfo    = lgTotal > 0 ? t('settings.importexport.importResultLogic', { n: lgTotal }) : ''
    const iconInfo  = (data.icons_imported ?? 0) > 0 ? t('settings.importexport.importResultIcons', { n: data.icons_imported }) : ''
    const visuInfo  = (data.visu_nodes_upserted ?? 0) > 0 ? t('settings.importexport.importResultVisu', { n: data.visu_nodes_upserted }) : ''
    importResult.value = { ok: true, text: t('settings.importexport.importResultOk', { objects: data.datapoints_created + data.datapoints_updated, bindings: data.bindings_created + data.bindings_updated }) + gaInfo + lgInfo + iconInfo + visuInfo }
    await loadFaSettings()
    if ((data.icons_imported ?? 0) > 0) await loadIcons()
  } catch (err) {
    importResult.value = { ok: false, text: err.response?.data?.detail ?? t('settings.importexport.importFailed') }
  }
}

async function onImportDbFile(e) {
  const file = e.target.files[0]; if (!file) return
  importDbFileName.value = file.name
  importDbResult.value = null
  try {
    const { data } = await configApi.importDb(file)
    importDbResult.value = { ok: true, text: t('settings.importexport.dbImportResultOk', { message: data.message ?? '', adapters: data.adapters_restarted ?? 0 }) }
  } catch (err) {
    importDbResult.value = { ok: false, text: err.response?.data?.detail ?? t('settings.importexport.dbImportFailed') }
  }
}

// ── Autobackup ──────────────────────────────────────────────────────────────
const autobackupCfg        = ref({ enabled: false, hour: 3, retention_days: 7 })
const autobackupList       = ref([])
const selectedAutobackup   = ref('')
const autobackupRunning    = ref(false)
const autobackupRestoring  = ref(false)
const autobackupMsg        = ref(null)
const autobackupRestoreMsg = ref(null)

async function loadAutobackupConfig() {
  try { const { data } = await autobackupApi.getConfig(); autobackupCfg.value = data } catch {}
}

async function loadAutobackupList() {
  try { const { data } = await autobackupApi.list(); autobackupList.value = data } catch {}
}

async function saveAutobackupConfig() {
  try { await autobackupApi.setConfig(autobackupCfg.value) } catch {}
}

async function runAutobackupNow() {
  autobackupRunning.value = true; autobackupMsg.value = null
  try {
    const { data } = await autobackupApi.runNow()
    autobackupMsg.value = { ok: true, text: t('settings.importexport.autobackupCreated', { name: formatAutobackupName(data.name) }) }
    await loadAutobackupList()
  } catch (err) {
    autobackupMsg.value = { ok: false, text: err.response?.data?.detail ?? t('settings.importexport.autobackupFailed') }
  } finally { autobackupRunning.value = false }
}

async function restoreAutobackup() {
  if (!selectedAutobackup.value) return
  autobackupRestoring.value = true; autobackupRestoreMsg.value = null
  try {
    const { data } = await autobackupApi.restore(selectedAutobackup.value)
    const errInfo = data.errors?.length ? ` ${t('settings.importexport.autobackupRestoreWarnSuffix', { n: data.errors.length })}` : ''
    autobackupRestoreMsg.value = { ok: true, text: t('settings.importexport.autobackupRestoreOk', { objects: data.datapoints, bindings: data.bindings, visu: data.visu_nodes }) + errInfo }
    await loadFaSettings()
    await loadIcons()
  } catch (err) {
    autobackupRestoreMsg.value = { ok: false, text: err.response?.data?.detail ?? t('settings.importexport.autobackupRestoreFailed') }
  } finally { autobackupRestoring.value = false }
}

function formatAutobackupName(name) {
  // "20240506-0300" → "06.05.2024 03:00"
  const m = name.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})$/)
  if (!m) return name
  return `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]} Uhr`
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// ── KNX Projekt Import ──────────────────────────────────────────────────────
const knxFile             = ref(null)
const knxPassword         = ref('')
const knxImporting        = ref(false)
const knxResult           = ref(null)
const knxGaCount          = ref(0)
const knxCreateDps        = ref(false)
const knxAdapterName      = ref('')
const knxDirection        = ref('SOURCE')
const knxAdapterInstances = ref([])

async function loadKnxGaCount() {
  try {
    const { data } = await knxprojApi.listGA({ size: 1 })
    knxGaCount.value = data.total || 0
  } catch { knxGaCount.value = 0 }
}

async function loadKnxAdapterInstances() {
  try {
    const { data } = await adapterApi.listInstances()
    knxAdapterInstances.value = (data || []).filter(i => i.adapter_type?.toUpperCase() === 'KNX')
    if (knxAdapterInstances.value.length === 1) {
      knxAdapterName.value = knxAdapterInstances.value[0].name
    }
  } catch { knxAdapterInstances.value = [] }
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
    const params = {}
    if (knxCreateDps.value && knxAdapterName.value) {
      params.adapter_name = knxAdapterName.value
      params.direction    = knxDirection.value
    }
    const { data } = await knxprojApi.import(fd, params)
    let msg = t('settings.importexport.knxImportResultOk', { n: data.imported })
    if (data.created  > 0) msg += t('settings.importexport.knxImportResultCreated',   { n: data.created })
    if (data.updated  > 0) msg += t('settings.importexport.knxImportResultUpdated',   { n: data.updated })
    if (data.locations > 0) msg += t('settings.importexport.knxImportResultLocations', { n: data.locations })
    if (data.trades   > 0) msg += t('settings.importexport.knxImportResultTrades',    { n: data.trades })
    knxResult.value = { ok: true, text: msg }
    await loadKnxGaCount()
  } catch (err) {
    const resp = err.response?.data
    const code = resp?.error_code
    const text = code && te(`settings.importexport.error_${code}`)
      ? t(`settings.importexport.error_${code}`)
      : (typeof resp?.detail === 'string' ? resp.detail : null) ?? t('settings.importexport.importFailed')
    knxResult.value = { ok: false, text }
  } finally {
    knxImporting.value = false
  }
}


onMounted(async () => {
  if (auth.isAdmin) await loadUsers()
  await loadKeys()
  await loadKnxGaCount()
  await loadKnxAdapterInstances()
  await loadAutobackupConfig()
  await loadAutobackupList()
})
// Note: timezone onMounted is defined above (merged there)

// ── Danger Zone ────────────────────────────────────────────────────────────
const showDzConfirm   = ref(false)
const dzTarget        = ref(null)
const resetResult     = ref(null)

const DZ_CONFIG = {
  bindings: {
    action: async () => {
      const { data } = await configApi.resetBindings()
      return t('settings.dangerzone.bindings.result', { n: data.deleted })
    },
    after: () => {},
  },
  datapoints: {
    action: async () => {
      const { data } = await configApi.resetDatapoints()
      return t('settings.dangerzone.datapoints.result', { dp: data.deleted, bindings: data.bindings_deleted })
    },
    after: () => {},
  },
  logic: {
    action: async () => {
      const { data } = await configApi.resetLogic()
      return t('settings.dangerzone.logic.result', { n: data.deleted })
    },
    after: () => {},
  },
  adapters: {
    action: async () => {
      const { data } = await configApi.resetAdapters()
      return t('settings.dangerzone.adapters.result', { adapters: data.deleted, bindings: data.bindings_deleted })
    },
    after: () => {},
  },
  knxga: {
    action: async () => {
      await knxprojApi.clearGA()
      return t('settings.dangerzone.knxga.result')
    },
    after: () => { knxGaCount.value = 0 },
  },
  all: {
    action: async () => {
      const { data } = await configApi.reset()
      const iconInfo = (data.icons_deleted ?? 0) > 0 ? t('settings.dangerzone.factory.resultIcons', { n: data.icons_deleted }) : ''
      return t('settings.dangerzone.factory.result', { dp: data.datapoints_deleted, bindings: data.bindings_deleted, adapters: data.adapter_instances_deleted, knxga: data.knx_group_addresses_deleted, logic: data.logic_graphs_deleted }) + iconInfo
    },
    after: () => { knxGaCount.value = 0; faSavedKey.value = null; icons.value = [] },
  },
}

const dzConfirmTitle   = computed(() => dzTarget.value ? t(`settings.dangerzone.${dzTarget.value}.confirmTitle`) : '')
const dzConfirmMessage = computed(() => dzTarget.value ? t(`settings.dangerzone.${dzTarget.value}.confirmMessage`) : '')
const dzConfirmLabel   = computed(() => dzTarget.value ? t(`settings.dangerzone.${dzTarget.value}.confirmLabel`) : t('common.delete'))

function showConfirm(target) {
  dzTarget.value = target
  resetResult.value = null
  showDzConfirm.value = true
}

async function doDzAction() {
  const cfg = DZ_CONFIG[dzTarget.value]
  if (!cfg) return
  try {
    const text = await cfg.action()
    cfg.after()
    resetResult.value = { ok: true, text }
  } catch (err) {
    resetResult.value = { ok: false, text: err.response?.data?.detail ?? t('common.deleteError') }
  }
}

// ── Icons Library ──────────────────────────────────────────────────────────
const icons         = ref([])
const iconsLoading  = ref(false)
const iconsUploading = ref(false)
const iconsSelected = ref(new Set())
const iconsSearch   = ref('')
const iconsMsg      = ref(null)
const iconsDragOver = ref(false)

// KNX UF Iconset
const knxufImporting = ref(false)
const knxufMsg       = ref(null)

async function doKnxufImport() {
  knxufImporting.value = true; knxufMsg.value = null
  try {
    const { data } = await iconsApi.importKnxuf()
    knxufMsg.value = { ok: data.imported > 0, text: data.message }
    if (data.imported > 0) await loadIcons()
  } catch (e) {
    knxufMsg.value = { ok: false, text: e.response?.data?.detail ?? t('settings.icons.knxufFailed') }
  } finally {
    knxufImporting.value = false
  }
}

// FontAwesome form
const faIconNames = ref('')
const faStyle     = ref('solid')
const faApiKey    = ref('')       // Eingabefeld (neu eingeben)
const faSavedKey  = ref(null)     // gespeicherter Key aus DB (null = keiner)
const faImporting = ref(false)
const faMsg       = ref(null)

async function loadFaSettings() {
  try {
    const { data } = await iconsApi.getSettings()
    faSavedKey.value = data.fa_api_key || null
  } catch { /* ignorieren */ }
}

async function doSaveFaKey() {
  const key = faApiKey.value.trim()
  if (!key) return
  try {
    await iconsApi.saveSettings({ fa_api_key: key })
    faSavedKey.value = key
    faApiKey.value = ''
    faMsg.value = { ok: true, text: t('settings.icons.faSaved'), debug: [] }
  } catch (e) {
    faMsg.value = { ok: false, text: e.response?.data?.detail ?? t('common.saveError'), debug: [] }
  }
}

async function doDeleteFaKey() {
  try {
    await iconsApi.saveSettings({ fa_api_key: null })
    faSavedKey.value = null
    faApiKey.value = ''
    faMsg.value = { ok: true, text: t('settings.icons.faDeleted'), debug: [] }
  } catch (e) {
    faMsg.value = { ok: false, text: e.response?.data?.detail ?? t('common.deleteError'), debug: [] }
  }
}

const iconsFiltered = computed(() => {
  const q = iconsSearch.value.toLowerCase()
  if (!q) return icons.value
  return icons.value.filter(i => i.name.toLowerCase().includes(q))
})

async function loadIcons() {
  iconsLoading.value = true
  // iconsMsg wird hier NICHT geleert — jede aufrufende Operation
  // setzt ihre eigene Meldung und würde sie sonst sofort wieder verlieren.
  try {
    const { data } = await iconsApi.list()
    icons.value = data.icons ?? []
    // Remove stale selections
    const names = new Set(icons.value.map(i => i.name))
    iconsSelected.value = new Set([...iconsSelected.value].filter(n => names.has(n)))
  } catch (e) {
    iconsMsg.value = { ok: false, text: e.response?.data?.detail ?? t('settings.icons.loadError') }
  } finally {
    iconsLoading.value = false
  }
}

function iconsToggle(name) {
  const sel = new Set(iconsSelected.value)
  if (sel.has(name)) sel.delete(name)
  else sel.add(name)
  iconsSelected.value = sel
}

function iconsSelectAll() {
  if (iconsSelected.value.size === icons.value.length) {
    iconsSelected.value = new Set()
  } else {
    iconsSelected.value = new Set(icons.value.map(i => i.name))
  }
}

async function _uploadFiles(fileList) {
  if (!fileList.length) return
  iconsUploading.value = true
  iconsMsg.value = null
  try {
    const fd = new FormData()
    for (const file of fileList) fd.append('files', file)
    const { data } = await iconsApi.import(fd)
    iconsMsg.value = { ok: true, text: data.message }
    await loadIcons()
  } catch (e) {
    iconsMsg.value = { ok: false, text: e.response?.data?.detail ?? 'Upload fehlgeschlagen' }
  } finally {
    iconsUploading.value = false
  }
}

function onIconsFileSelect(e) {
  _uploadFiles([...e.target.files])
  e.target.value = ''
}

function onIconsDrop(e) {
  iconsDragOver.value = false
  _uploadFiles([...e.dataTransfer.files])
}

async function doIconsDelete() {
  if (!iconsSelected.value.size) return
  iconsMsg.value = null
  try {
    const names = [...iconsSelected.value]
    await iconsApi.delete(names)
    iconsSelected.value = new Set()
    iconsMsg.value = { ok: true, text: t('settings.icons.deleted', { n: names.length }) }
    await loadIcons()
  } catch (e) {
    iconsMsg.value = { ok: false, text: e.response?.data?.detail ?? t('common.deleteError') }
  }
}

async function doIconsExport() {
  if (!iconsSelected.value.size) return
  try {
    const names = [...iconsSelected.value]
    const { data: blob } = await iconsApi.export(names)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'obs_icons.zip'; a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    iconsMsg.value = { ok: false, text: e.response?.data?.detail ?? t('common.deleteError') }
  }
}

async function doFaImport() {
  const names = faIconNames.value.split(',').map(s => s.trim()).filter(Boolean)
  if (!names.length) return
  faImporting.value = true; faMsg.value = null
  try {
    const payload = { icons: names, style: faStyle.value }
    if (faApiKey.value.trim()) payload.api_key = faApiKey.value.trim()
    const { data } = await iconsApi.importFa(payload)
    faMsg.value = { ok: data.imported > 0, text: data.message, debug: data.debug ?? [] }
    if (data.imported > 0) faIconNames.value = ''
    await loadIcons()
  } catch (e) {
    faMsg.value = { ok: false, text: e.response?.data?.detail ?? t('settings.icons.faImportFailed'), debug: [] }
  } finally {
    faImporting.value = false
  }
}
</script>
