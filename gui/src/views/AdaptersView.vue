<template>
  <div class="flex flex-col gap-5">
    <!-- Demo-Modus Banner -->
    <div v-if="isDemo" class="flex items-center gap-3 p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg text-sm text-amber-600 dark:text-amber-400">
      <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m0 0v2m0-2h2m-2 0H10m2-11a7 7 0 110 14A7 7 0 0112 4z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v4"/></svg>
      {{ $t('adapters.demoMode') }}
    </div>

    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ $t('adapters.title') }}</h2>
        <p class="text-sm text-slate-500 mt-0.5">{{ $t('adapters.subtitle') }}</p>
      </div>
      <button v-if="!isDemo" @click="openCreate" class="btn-primary btn-sm" data-testid="btn-new-instance">
        {{ $t('adapters.newInstance') }}
      </button>
    </div>

    <div v-if="store.loading" class="flex justify-center py-20"><Spinner size="lg" /></div>

    <div v-else class="flex flex-col gap-4">

      <!-- Neue Instanz erstellen -->
      <div v-if="creating" class="card border border-blue-500/40">
        <div class="card-header">
          <h3 class="font-semibold text-slate-800 dark:text-slate-100">{{ $t('adapters.createTitle') }}</h3>
          <button @click="cancelCreate" class="btn-icon">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="p-5 flex flex-col gap-4">
          <div class="grid grid-cols-2 gap-4">
            <div class="form-group">
              <label class="label">{{ $t('adapters.adapterType') }}</label>
              <select v-model="newForm.adapter_type" class="input" required @change="onTypeChange" data-testid="select-adapter-type">
                <option value="">{{ $t('adapters.selectType') }}</option>
                <option v-for="t in availableTypes" :key="t" :value="t">{{ t }}</option>
              </select>
              <p v-if="availableTypesErr" class="text-xs text-red-400 mt-1">{{ $t('adapters.typesError') }}</p>
            </div>
            <div class="form-group">
              <label class="label">{{ $t('adapters.name') }}</label>
              <input v-model="newForm.name" type="text" class="input" :placeholder="$t('adapters.namePlaceholder')" data-testid="input-instance-name" />
            </div>
          </div>

          <!-- Schema-based config form -->
          <div v-if="newForm.adapter_type && newSchema">
            <label class="label mb-2">{{ $t('adapters.configLabel') }}</label>
            <AnwesenheitConfigForm
              v-if="newForm.adapter_type === 'ANWESENHEITSSIMULATION'"
              v-model="newForm.config"
            />
            <KnxConfigForm
              v-else-if="newForm.adapter_type === 'KNX'"
              v-model="newForm.config"
            />
            <MessageConfigForm
              v-else-if="newForm.adapter_type === 'MESSAGE'"
              v-model="newForm.config"
            />
            <template v-else>
              <SchemaForm
                :schema="newSchema"
                v-model="newForm.config"
                :adapter-type="newForm.adapter_type"
                :exclude="newForm.adapter_type.toLowerCase() === 'zeitschaltuhr' ? ['custom_holidays'] : []"
              />
              <ZeitschaltuhrCustomHolidaysEditor
                v-if="newForm.adapter_type.toLowerCase() === 'zeitschaltuhr'"
                :model-value="newForm.config.custom_holidays ?? []"
                @update:model-value="newForm.config.custom_holidays = $event"
              />
            </template>
          </div>
          <div v-else-if="newForm.adapter_type && schemaLoading" class="flex items-center gap-2 text-sm text-slate-500">
            <Spinner size="xs" /> {{ $t('adapters.schemaLoading') }}
          </div>

          <div v-if="createError" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
            {{ createError }}
          </div>
          <div class="flex gap-3">
            <button @click="cancelCreate" class="btn-secondary btn-sm">{{ $t('common.cancel') }}</button>
            <button @click="submitCreate" class="btn-primary btn-sm" :disabled="creating === 'saving'" data-testid="btn-save-instance">
              <Spinner v-if="creating === 'saving'" size="xs" color="white" />
              {{ $t('adapters.create') }}
            </button>
          </div>
        </div>
      </div>

      <!-- Bestehende Instanzen -->
      <div v-if="store.instances.length === 0 && !creating" class="card p-8 text-center text-slate-500">
        {{ $t('adapters.noInstances') }}
      </div>

      <div v-for="a in store.instances" :key="a.id" class="card" :data-testid="`adapter-row-${a.id}`">
        <!-- Card Header -->
        <div class="card-header">
          <div class="flex items-center gap-3 min-w-0">
            <span :class="['w-3 h-3 rounded-full shrink-0', dotClass(a)]" :data-testid="`adapter-dot-${a.id}`" />
            <h3 class="font-semibold text-slate-800 dark:text-slate-100 truncate">{{ a.name }}</h3>
            <Badge variant="info" size="xs">{{ a.adapter_type }}</Badge>
            <Badge :variant="statusBadgeVariant(a)" size="xs" :data-testid="`adapter-status-badge-${a.id}`">
              {{ $t(statusLabel(a)) }}
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
          <span>{{ $t('adapters.bindings') }}: <span class="text-slate-600 dark:text-slate-300 font-medium">{{ a.bindings }}</span></span>
          <span v-if="!a.registered" class="text-amber-400">{{ $t('adapters.typeNotRegistered') }}</span>
        </div>

        <!-- Status-Detail bei Warning/Error -->
        <div
          v-if="a.severity && a.severity !== 'ok' && (a.status_detail || a.status_detail_code)"
          :class="[
            'mx-5 mb-3 flex items-start gap-2 p-3 rounded-lg text-sm',
            a.severity === 'error'
              ? 'bg-red-500/10 border border-red-500/30 text-red-500 dark:text-red-400'
              : 'bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400',
          ]"
          :data-testid="`adapter-status-detail-${a.id}`"
        >
          <svg class="w-4 h-4 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
          </svg>
          <span>{{ statusDetailText(a) }}</span>
        </div>

        <!-- Expanded Config Panel -->
        <div v-if="expanded[a.id]" class="border-t border-slate-200 dark:border-slate-700/60 p-5 flex flex-col gap-4">
          <div :class="{ 'pointer-events-none select-none opacity-50': isDemo }">
            <div class="form-group">
              <label class="label">{{ $t('adapters.nameLabel') }}</label>
              <input v-model="drafts[a.id].name" type="text" class="input" />
            </div>

            <!-- Anwesenheitssimulation: History-Verfügbarkeit -->
            <div v-if="a.adapter_type === 'ANWESENHEITSSIMULATION' && anwesenheitHealth[a.id]" :class="[
              'mt-4 flex items-start gap-2 p-3 rounded-lg text-sm',
              anwesenheitHealth[a.id].healthy
                ? 'bg-green-500/10 border border-green-500/30 text-green-400'
                : 'bg-amber-500/10 border border-amber-500/30 text-amber-400'
            ]">
              <svg class="w-4 h-4 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path v-if="anwesenheitHealth[a.id].healthy" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
                <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
              </svg>
              {{ anwesenheitHealth[a.id].message }}
            </div>

            <!-- Schema-based config form -->
            <div v-if="schemas[a.adapter_type]" class="mt-4">
              <label class="label mb-2">{{ $t('adapters.configLabel') }}</label>
              <AnwesenheitConfigForm
                v-if="a.adapter_type === 'ANWESENHEITSSIMULATION'"
                v-model="drafts[a.id].config"
              />
              <KnxConfigForm
                v-else-if="a.adapter_type === 'KNX'"
                v-model="drafts[a.id].config"
              />
              <MessageConfigForm
                v-else-if="a.adapter_type === 'MESSAGE'"
                v-model="drafts[a.id].config"
              />
              <template v-else>
                <SchemaForm
                  :schema="schemas[a.adapter_type]"
                  v-model="drafts[a.id].config"
                  :adapter-type="a.adapter_type"
                  :exclude="a.adapter_type.toLowerCase() === 'zeitschaltuhr' ? ['custom_holidays'] : []"
                />
                <ZeitschaltuhrCustomHolidaysEditor
                  v-if="a.adapter_type.toLowerCase() === 'zeitschaltuhr'"
                  :model-value="drafts[a.id].config.custom_holidays ?? []"
                  @update:model-value="drafts[a.id].config.custom_holidays = $event"
                />
              </template>
            </div>
            <div v-else class="flex items-center gap-2 text-sm text-slate-500 mt-4">
              <Spinner size="xs" /> {{ $t('adapters.schemaLoading') }}
            </div>

            <div class="flex items-center gap-2 mt-4">
              <input type="checkbox" :id="'enabled-' + a.id" v-model="drafts[a.id].enabled" class="w-4 h-4 rounded" />
              <label :for="'enabled-' + a.id" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('adapters.enabled') }}</label>
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
            {{ feedbackText(feedback[a.id]) }}
          </div>

          <div v-if="!isDemo" class="flex gap-3 flex-wrap">
            <button v-if="a.adapter_type !== 'ANWESENHEITSSIMULATION' && a.adapter_type !== 'SNMP' && a.adapter_type !== 'MESSAGE'" @click="testConnection(a)" class="btn-secondary btn-sm" :disabled="busy[a.id] === 'test'"
              :title="$t('adapters.testConnectionTitle')">
              <Spinner v-if="busy[a.id] === 'test'" size="xs" color="slate" />
              {{ $t('adapters.testConnection') }}
            </button>
            <button @click="saveInstance(a)" class="btn-primary btn-sm" :disabled="busy[a.id] === 'save'"
              :title="$t('adapters.saveTitle')">
              <Spinner v-if="busy[a.id] === 'save'" size="xs" color="white" />
              {{ $t('common.save') }}
            </button>
            <button v-if="a.adapter_type !== 'ANWESENHEITSSIMULATION'" @click="restartInstance(a)" class="btn-secondary btn-sm" :disabled="busy[a.id] === 'restart'"
              :title="$t('adapters.reconnectTitle')">
              <Spinner v-if="busy[a.id] === 'restart'" size="xs" color="slate" />
              {{ $t('adapters.reconnect') }}
            </button>
            <button v-if="a.adapter_type === 'IOBROKER'" @click="openIoBrokerImport(a)" class="btn-secondary btn-sm" :disabled="!a.connected"
              :title="$t('adapters.importTitle')">
              {{ $t('adapters.importBtn') }}
            </button>
            <button v-if="a.adapter_type === 'ANWESENHEITSSIMULATION'" @click="openAnwesenheitSelector(a)" class="btn-secondary btn-sm"
              :title="$t('adapters.manageObjectsTitle')">
              {{ $t('adapters.manageObjects') }}
            </button>
            <button
              @click="openBindingMigration(a)"
              class="btn-secondary btn-sm"
              :disabled="migrationTargetsFor(a).length === 0"
              :data-testid="`btn-open-migrate-bindings-${a.id}`"
              :title="$t('adapters.migration.openTitle')">
              {{ $t('adapters.migration.openButton') }}
            </button>
            <button @click="confirmDelete(a)" class="ml-auto btn-danger btn-sm" :disabled="busy[a.id] === 'delete'"
              :title="$t('adapters.deleteConfirm')"
              data-testid="btn-delete-instance">
              <Spinner v-if="busy[a.id] === 'delete'" size="xs" color="white" />
              {{ $t('common.delete') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Löschen bestätigen -->
    <ConfirmDialog
      v-model="showDeleteConfirm"
      :title="deleteTarget ? $t('adapters.deleteInstanceTitle', { name: deleteTarget.name }) : ''"
      :message="$t('adapters.allBindings')"
      :confirm-label="$t('common.delete')"
      @confirm="executeDelete"
    />

    <!-- Anwesenheitssimulation: Objekte verwalten -->
    <Modal v-model="anwesenheitOpen" :title="anwesenheitInstance ? $t('adapters.anwesenheit.modalTitleFull', { name: anwesenheitInstance.name }) : $t('adapters.anwesenheit.modalTitle')" max-width="xl">
      <AnwesenheitDatapointSelector v-if="anwesenheitOpen && anwesenheitInstance" :instance-id="anwesenheitInstance.id" />
    </Modal>

    <Modal
      v-model="migrationOpen"
      :title="migrationSource ? $t('adapters.migration.modalTitleFull', { name: migrationSource.name }) : $t('adapters.migration.modalTitle')"
      max-width="lg">
      <div class="flex flex-col gap-4">
        <p class="text-sm text-slate-500">
          {{ $t('adapters.migration.description') }}
        </p>
        <div class="form-group">
          <label class="label">{{ $t('adapters.migration.targetLabel') }}</label>
          <select v-model="migrationTargetId" class="input" data-testid="select-migration-target">
            <option value="">{{ $t('adapters.migration.targetPlaceholder') }}</option>
            <option
              v-for="item in migrationSourceTargets"
              :key="item.id"
              :value="item.id">
              {{ item.name }} ({{ $t('adapters.migration.targetBindingCount', { n: item.bindings }) }})
            </option>
          </select>
        </div>

        <div v-if="migrationError" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
          {{ migrationError }}
        </div>
        <div
          v-if="migrationResult"
          class="p-3 bg-green-500/10 border border-green-500/30 rounded-lg text-sm text-green-500"
          data-testid="migration-result">
          {{ $t('adapters.migration.result', { migrated: migrationResult.migrated, skipped: migrationResult.skipped }) }}
        </div>

        <div class="flex gap-3">
          <button class="btn-secondary btn-sm" @click="migrationOpen = false">{{ $t('common.cancel') }}</button>
          <button
            class="btn-primary btn-sm"
            :disabled="migrationBusy || !migrationTargetId"
            data-testid="btn-migrate-bindings-confirm"
            @click="executeBindingMigration">
            <Spinner v-if="migrationBusy" size="xs" color="white" />
            {{ $t('adapters.migration.confirmButton') }}
          </button>
        </div>
      </div>
    </Modal>

    <Modal v-model="importOpen" :title="importInstance ? $t('adapters.iobroker.modalTitleFull', { name: importInstance.name }) : $t('adapters.iobroker.modalTitle')" max-width="2xl" resizable>
      <div class="flex flex-col gap-4">
        <div class="grid grid-cols-[1fr_auto] gap-3">
          <div class="form-group">
            <label class="label">{{ $t('adapters.iobroker.prefixLabel') }}</label>
            <input v-model="importForm.prefix" class="input font-mono text-sm" :placeholder="$t('adapters.iobroker.prefixPlaceholder')" @keyup.enter="loadImportPreview" />
            <p class="hint">{{ $t('adapters.iobroker.prefixHint') }}</p>
          </div>
          <div class="form-group">
            <label class="label">{{ $t('adapters.iobroker.limit') }}</label>
            <input v-model.number="importForm.limit" type="number" min="1" max="500" class="input w-24" />
          </div>
        </div>

        <div class="grid grid-cols-3 gap-3">
          <div class="form-group">
            <label class="label">{{ $t('adapters.iobroker.direction') }}</label>
            <select v-model="importForm.direction" class="input">
              <option value="auto">{{ $t('adapters.iobroker.directionAuto') }}</option>
              <option value="SOURCE">{{ $t('adapters.iobroker.directionRead') }}</option>
              <option value="BOTH">{{ $t('adapters.iobroker.directionReadWrite') }}</option>
              <option value="DEST">{{ $t('adapters.iobroker.directionWrite') }}</option>
            </select>
          </div>
          <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300 mt-7">
            <input type="checkbox" v-model="importForm.persist_value" class="w-4 h-4 rounded" />
            {{ $t('datapoints.form.persistValue') }}
          </label>
          <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300 mt-7">
            <input type="checkbox" v-model="importForm.record_history" class="w-4 h-4 rounded" />
            {{ $t('datapoints.detail.recordHistory') }}
          </label>
        </div>

        <div class="flex gap-3">
          <button @click="loadImportPreview" class="btn-secondary btn-sm" :disabled="importBusy">
            <Spinner v-if="importBusy === 'preview'" size="xs" color="slate" />
            {{ $t('adapters.iobroker.loadPreview') }}
          </button>
          <button @click="executeIoBrokerImport" class="btn-primary btn-sm" :disabled="importBusy || selectedImportCount === 0">
            <Spinner v-if="importBusy === 'import'" size="xs" color="white" />
            {{ $t('adapters.iobroker.importCount', { n: selectedImportCount }) }}
          </button>
        </div>

        <div v-if="importError" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">{{ importError }}</div>
        <div v-if="importResult" class="p-3 bg-green-500/10 border border-green-500/30 rounded-lg text-sm text-green-500">
          {{ $t('adapters.iobroker.importResult', { datapoints: importResult.created_datapoints, bindings: importResult.created_bindings, skipped: importResult.skipped_existing }) }}
        </div>

        <div v-if="importPreview.length" class="border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
          <div class="px-3 py-2 bg-slate-50 dark:bg-slate-800/60 flex items-center justify-between text-sm">
            <label class="flex items-center gap-2">
              <input type="checkbox" :checked="allImportSelected" @change="toggleAllImport($event.target.checked)" class="w-4 h-4 rounded" />
              {{ $t('adapters.iobroker.selectAll') }}
            </label>
            <span class="text-slate-500">{{ $t('adapters.iobroker.hits', { n: importPreview.length }) }}</span>
          </div>
          <div class="max-h-96 overflow-y-auto divide-y divide-slate-100 dark:divide-slate-700/50">
            <label v-for="item in importPreview" :key="item.state_id" class="flex gap-3 px-3 py-2 hover:bg-slate-50 dark:hover:bg-slate-800/50" :class="{ 'opacity-50': item.exists }">
              <input type="checkbox" v-model="selectedImportStates" :value="item.state_id" :disabled="item.exists" class="w-4 h-4 rounded mt-1" />
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2 min-w-0">
                  <span class="font-mono text-sm truncate">{{ item.state_id }}</span>
                  <Badge variant="info" size="xs">{{ item.data_type }}</Badge>
                  <Badge v-if="item.direction === 'BOTH'" variant="success" size="xs">rw</Badge>
                  <Badge v-else variant="muted" size="xs">{{ item.direction }}</Badge>
                </div>
                <div class="text-xs text-slate-500 truncate">{{ item.name }} · {{ item.tags.join(', ') }}</div>
                <div v-if="item.reason" class="text-xs text-amber-500">{{ item.reason }}</div>
              </div>
            </label>
          </div>
        </div>
      </div>
    </Modal>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { adapterApi } from '@/api/client'
import { useAdapterStore } from '@/stores/adapters'
import { useAuthStore } from '@/stores/auth'
import Badge         from '@/components/ui/Badge.vue'
import Spinner       from '@/components/ui/Spinner.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import SchemaForm    from '@/components/adapters/SchemaForm.vue'
import ZeitschaltuhrCustomHolidaysEditor from '@/components/adapters/ZeitschaltuhrCustomHolidaysEditor.vue'
import AnwesenheitDatapointSelector from '@/components/adapters/AnwesenheitDatapointSelector.vue'
import AnwesenheitConfigForm from '@/components/adapters/AnwesenheitConfigForm.vue'
import KnxConfigForm        from '@/components/adapters/KnxConfigForm.vue'
import MessageConfigForm    from '@/components/adapters/MessageConfigForm.vue'
import Modal         from '@/components/ui/Modal.vue'
import { adapterDotClass as dotClass, adapterBadgeVariant as statusBadgeVariant, adapterStatusLabel as statusLabel, adapterStatusDetailText } from '@/utils/adapterStatus'

const { t, te } = useI18n()
const statusDetailText = (a) => adapterStatusDetailText(a, t, te)
// Issue #779: TestResult / action feedback may carry a backend detail_code
// (adapters.testResult.*) with params; translate it, else show the raw detail.
function feedbackText(fb) {
  if (!fb) return ''
  const code = fb.detail_code
  if (code) {
    const key = `adapters.testResult.${code}`
    if (te(key)) return t(key, fb.detail_params ?? {})
  }
  return fb.detail ?? ''
}
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

// Binding-Migration
const migrationOpen = ref(false)
const migrationSource = ref(null)
const migrationTargetId = ref('')
const migrationBusy = ref(false)
const migrationError = ref(null)
const migrationResult = ref(null)

// Anwesenheitssimulation — Objekte verwalten + Health
const anwesenheitOpen     = ref(false)
const anwesenheitInstance = ref(null)
const anwesenheitHealth   = reactive({})  // id → { healthy, message, ... }

function openAnwesenheitSelector(a) {
  anwesenheitInstance.value = a
  anwesenheitOpen.value = true
}

async function loadAnwesenheitHealth(a) {
  try {
    const { data } = await adapterApi.anwesenheitHealth(a.id)
    anwesenheitHealth[a.id] = data
  } catch {
    // silently ignore — health is informational only
  }
}

// ioBroker Import
const importOpen = ref(false)
const importInstance = ref(null)
const importBusy = ref(null)
const importError = ref(null)
const importResult = ref(null)
const importPreview = ref([])
const selectedImportStates = ref([])
const importForm = reactive({
  prefix: '',
  direction: 'auto',
  persist_value: true,
  record_history: true,
  limit: 300,
})

const selectedImportCount = computed(() => selectedImportStates.value.length)
const allImportSelected = computed(() => {
  const selectable = importPreview.value.filter(i => !i.exists).map(i => i.state_id)
  return selectable.length > 0 && selectable.every(id => selectedImportStates.value.includes(id))
})
const migrationSourceTargets = computed(() => {
  const src = migrationSource.value
  if (!src) return []
  return store.instances.filter((item) => item.id !== src.id && item.adapter_type === src.adapter_type)
})

let refreshTimer = null

// ------------------------------------------------------------------

async function refreshInstances({ silent = false } = {}) {
  await store.fetchAdapters({ silent })
  initDrafts()
  for (const a of store.instances) {
    const fb = feedback[a.id]
    if (a.connected && fb && fb.success === false) {
      delete feedback[a.id]
    }
  }
}

onMounted(async () => {
  await refreshInstances()
  try {
    availableTypes.value = await store.fetchTypes()
  } catch {
    availableTypesErr.value = true
  }
  refreshTimer = window.setInterval(() => refreshInstances({ silent: true }), 10000)
})

onBeforeUnmount(() => {
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
    refreshTimer = null
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
    if (a.adapter_type === 'ANWESENHEITSSIMULATION') loadAnwesenheitHealth(a)
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
    createError.value = t('adapters.createValidation')
    return
  }
  creating.value = 'saving'
  try {
    const inst = await store.createInstance(newForm.adapter_type, newForm.name.trim(), newForm.config)
    drafts[inst.id] = { name: inst.name, config: { ...inst.config }, enabled: inst.enabled }
    creating.value = false
  } catch (e) {
    createError.value = e.response?.data?.detail ?? t('adapters.createError')
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
    await refreshInstances()
  } catch (e) {
    feedback[a.id] = { success: false, detail: e.response?.data?.detail ?? t('common.error') }
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
    feedback[a.id] = { success: true, detail: t('adapters.savedReconnected') }
    if (a.adapter_type === 'ANWESENHEITSSIMULATION') loadAnwesenheitHealth(a)
    await refreshInstances()
  } catch (e) {
    feedback[a.id] = { success: false, detail: e.response?.data?.detail ?? t('common.error') }
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
    feedback[a.id] = { success: true, detail: t('adapters.reconnected') }
    await refreshInstances()
  } catch (e) {
    feedback[a.id] = { success: false, detail: e.response?.data?.detail ?? t('common.error') }
  } finally {
    busy[a.id] = null
  }
}

// ---------- ioBroker Import ----------

function openIoBrokerImport(a) {
  importInstance.value = a
  importOpen.value = true
  importBusy.value = null
  importError.value = null
  importResult.value = null
  importPreview.value = []
  selectedImportStates.value = []
  importForm.prefix = ''
  importForm.direction = 'auto'
  importForm.persist_value = true
  importForm.record_history = true
  importForm.limit = 300
}

function importPayload(states = selectedImportStates.value) {
  return {
    prefix: importForm.prefix.trim(),
    states,
    direction: importForm.direction,
    persist_value: importForm.persist_value,
    record_history: importForm.record_history,
    limit: importForm.limit || 300,
  }
}

async function loadImportPreview() {
  if (!importInstance.value) return
  importBusy.value = 'preview'
  importError.value = null
  importResult.value = null
  try {
    const { data } = await adapterApi.iobrokerImportPreview(importInstance.value.id, importPayload([]))
    importPreview.value = data.preview || []
    selectedImportStates.value = importPreview.value.filter(i => !i.exists).map(i => i.state_id)
    if (!importPreview.value.length) importError.value = t('adapters.iobroker.noStates')
  } catch (e) {
    importError.value = e.response?.data?.detail ?? t('adapters.iobroker.previewError')
  } finally {
    importBusy.value = null
  }
}

function toggleAllImport(checked) {
  selectedImportStates.value = checked
    ? importPreview.value.filter(i => !i.exists).map(i => i.state_id)
    : []
}

async function executeIoBrokerImport() {
  if (!importInstance.value || selectedImportStates.value.length === 0) return
  importBusy.value = 'import'
  importError.value = null
  importResult.value = null
  try {
    const { data } = await adapterApi.iobrokerImport(importInstance.value.id, importPayload())
    importResult.value = data
    await store.fetchAdapters()
    initDrafts()
    await loadImportPreview()
  } catch (e) {
    importError.value = e.response?.data?.detail ?? t('adapters.iobroker.importFailed')
  } finally {
    importBusy.value = null
  }
}

function migrationTargetsFor(source) {
  return store.instances.filter((item) => item.id !== source.id && item.adapter_type === source.adapter_type)
}

function openBindingMigration(source) {
  migrationSource.value = source
  migrationTargetId.value = ''
  migrationError.value = null
  migrationResult.value = null
  migrationOpen.value = true
}

async function executeBindingMigration() {
  const source = migrationSource.value
  if (!source || !migrationTargetId.value) return
  migrationBusy.value = true
  migrationError.value = null
  migrationResult.value = null
  try {
    const { data } = await adapterApi.migrateBindings(source.id, migrationTargetId.value)
    migrationResult.value = data
    feedback[source.id] = {
      success: true,
      detail: t('adapters.migration.result', { migrated: data.migrated, skipped: data.skipped }),
    }
    await refreshInstances()
  } catch (e) {
    migrationError.value = e.response?.data?.detail ?? t('adapters.migration.failed')
  } finally {
    migrationBusy.value = false
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
    feedback[a.id] = { success: false, detail: e.response?.data?.detail ?? t('adapters.deleteError') }
  } finally {
    busy[a.id] = null
  }
}
</script>
