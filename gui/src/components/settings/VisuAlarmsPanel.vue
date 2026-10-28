<template>
  <div class="flex flex-col gap-4" data-testid="visu-alarms-panel">
    <div class="card">
      <div class="card-header flex items-center justify-between">
        <div>
          <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('settings.visuAlarms.title') }}</h3>
          <p class="text-xs text-slate-500 mt-0.5">{{ $t('settings.visuAlarms.desc') }}</p>
        </div>
        <div class="flex items-center gap-1">
          <HelpButton help-id="settings-visu-alarms" />
          <button type="button" class="btn-primary btn-sm" :disabled="loading || loadFailed" data-testid="btn-add-visu-alarm" @click="addAlarm">
            {{ $t('settings.visuAlarms.addButton') }}
          </button>
        </div>
      </div>

      <div class="card-body flex flex-col gap-3">
        <div v-if="loading" class="flex justify-center py-4"><Spinner /></div>

        <div v-else-if="alarms.length === 0" class="text-sm text-slate-500 py-4 text-center" data-testid="visu-alarms-empty">
          {{ $t('settings.visuAlarms.empty') }}
        </div>

        <div
          v-for="(alarm, index) in alarms"
          :key="alarm.id"
          class="rounded-lg border border-slate-200 dark:border-slate-700/60 bg-slate-50 dark:bg-slate-800/40"
          :data-testid="`visu-alarm-${index}`"
        >
          <!-- Kopfzeile -->
          <div class="flex items-center gap-2 px-3 py-2.5">
            <button
              type="button"
              class="flex-1 min-w-0 flex items-center gap-2 text-left"
              data-testid="btn-toggle-visu-alarm"
              @click="alarm._open = !alarm._open"
            >
              <span class="text-xs text-slate-400 w-3">{{ alarm._open ? '▾' : '▸' }}</span>
              <span class="w-6 text-center shrink-0"><VisuIcon :icon="alarm.icon || (alarm.display === 'page' ? '📄' : '🔔')" /></span>
              <span class="text-sm font-medium text-slate-800 dark:text-slate-100 truncate">{{ alarm.name || $t('settings.visuAlarms.unnamed') }}</span>
              <span v-if="rowError(alarm)" class="text-xs text-red-400 shrink-0">⚠</span>
            </button>
            <label class="flex items-center gap-1.5 text-xs text-slate-500 shrink-0 cursor-pointer">
              <input v-model="alarm.enabled" type="checkbox" class="w-4 h-4 rounded accent-blue-500" data-testid="check-visu-alarm-enabled" />
              {{ $t('settings.visuAlarms.enabled') }}
            </label>
            <button
              type="button"
              class="btn-ghost btn-sm text-slate-400 hover:text-red-500 shrink-0"
              :title="$t('settings.visuAlarms.deleteButtonTitle')"
              data-testid="btn-delete-visu-alarm"
              @click="alarms.splice(index, 1)"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7V4h6v3M4 7h16"/></svg>
            </button>
          </div>

          <div v-if="alarm._open" class="flex flex-col gap-4 px-3 pb-3 border-t border-slate-200 dark:border-slate-700/60 pt-3" data-testid="visu-alarm-form">
            <div class="form-group">
              <label class="label">{{ $t('settings.visuAlarms.name') }} <span class="text-red-400">*</span></label>
              <input v-model="alarm.name" type="text" class="input" maxlength="80" :placeholder="$t('settings.visuAlarms.namePlaceholder')" data-testid="input-visu-alarm-name" />
            </div>

            <!-- Auslöser -->
            <fieldset class="flex flex-col gap-2">
              <legend class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">{{ $t('settings.visuAlarms.trigger') }}</legend>
              <div class="form-group">
                <label class="label">{{ $t('settings.visuAlarms.datapoint') }} <span class="text-red-400">*</span></label>
                <DpCombobox
                  :model-value="alarm.datapoint_id"
                  :display-name="dpNames[alarm.datapoint_id] ?? ''"
                  data-testid="input-visu-alarm-datapoint"
                  @select="(dp) => selectDatapoint(alarm, dp)"
                />
              </div>
              <div class="flex gap-2 items-end">
                <div class="form-group w-36">
                  <label class="label">{{ $t('settings.visuAlarms.operator') }}</label>
                  <select v-model="alarm.operator" class="input" data-testid="select-visu-alarm-operator">
                    <option v-for="op in OPERATORS" :key="op" :value="op">{{ op }}</option>
                  </select>
                </div>
                <div class="form-group flex-1">
                  <label class="label">{{ $t('settings.visuAlarms.compareValue') }}</label>
                  <input v-model="alarm.compare_value" type="text" class="input" data-testid="input-visu-alarm-compare" />
                </div>
              </div>
              <p class="text-xs text-slate-500">{{ $t('settings.visuAlarms.triggerHint') }}</p>
            </fieldset>

            <!-- Anzeige -->
            <fieldset class="flex flex-col gap-2">
              <legend class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">{{ $t('settings.visuAlarms.display') }}</legend>
              <div class="flex gap-4 text-sm">
                <label class="flex items-center gap-1.5 cursor-pointer">
                  <input v-model="alarm.display" type="radio" value="message" data-testid="radio-visu-alarm-message" />
                  {{ $t('settings.visuAlarms.displayMessage') }}
                </label>
                <label class="flex items-center gap-1.5 cursor-pointer">
                  <input v-model="alarm.display" type="radio" value="page" data-testid="radio-visu-alarm-page" />
                  {{ $t('settings.visuAlarms.displayPage') }}
                </label>
              </div>

              <template v-if="alarm.display === 'message'">
                <div class="form-group">
                  <label class="label">{{ $t('settings.visuAlarms.messageTitle') }}</label>
                  <input v-model="alarm.title" type="text" class="input" maxlength="120" :placeholder="$t('settings.visuAlarms.messageTitlePlaceholder')" data-testid="input-visu-alarm-title" />
                </div>
                <div class="form-group">
                  <label class="label">{{ $t('settings.visuAlarms.messageText') }}</label>
                  <textarea v-model="alarm.text" class="input" rows="2" maxlength="500" data-testid="input-visu-alarm-text" />
                </div>
                <div class="form-group">
                  <label class="label">{{ $t('settings.visuAlarms.icon') }}</label>
                  <div class="p-3 rounded-lg border border-slate-200 dark:border-slate-700/60 bg-white dark:bg-slate-900/40">
                    <IconPicker v-model="alarm.icon" />
                  </div>
                </div>
                <div class="form-group w-48">
                  <label class="label">{{ $t('settings.visuAlarms.severity') }}</label>
                  <select v-model="alarm.severity" class="input" data-testid="select-visu-alarm-severity">
                    <option value="info">{{ $t('settings.visuAlarms.severityInfo') }}</option>
                    <option value="warning">{{ $t('settings.visuAlarms.severityWarning') }}</option>
                    <option value="critical">{{ $t('settings.visuAlarms.severityCritical') }}</option>
                  </select>
                </div>
              </template>

              <div v-else class="form-group">
                <label class="label">{{ $t('settings.visuAlarms.page') }} <span class="text-red-400">*</span></label>
                <select v-model="alarm.page_id" class="input" data-testid="select-visu-alarm-page">
                  <option value="">{{ $t('settings.visuAlarms.selectPage') }}</option>
                  <option v-if="alarm.page_id && !pageIds.has(alarm.page_id)" :value="alarm.page_id">{{ $t('settings.visuAlarms.missingPage') }}</option>
                  <option v-for="page in pageOptions" :key="page.id" :value="page.id">{{ page.label }}</option>
                </select>
                <p class="text-xs text-slate-500 mt-1">{{ $t('settings.visuAlarms.pageHint') }}</p>
              </div>
            </fieldset>

            <!-- Ende & Ton -->
            <fieldset class="flex flex-col gap-1.5">
              <legend class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">{{ $t('settings.visuAlarms.end') }}</legend>
              <label class="flex items-center gap-2 text-sm cursor-pointer">
                <input v-model="alarm.ack_button" type="checkbox" class="w-4 h-4 rounded accent-blue-500" data-testid="check-visu-alarm-ack" />
                {{ $t('settings.visuAlarms.ackButton') }}
              </label>
              <label class="flex items-center gap-2 text-sm cursor-pointer">
                <input v-model="alarm.end_on_clear" type="checkbox" class="w-4 h-4 rounded accent-blue-500" data-testid="check-visu-alarm-clear" />
                {{ $t('settings.visuAlarms.endOnClear') }}
              </label>
              <label class="flex items-center gap-2 text-sm cursor-pointer mt-1">
                <input v-model="alarm.sound" type="checkbox" class="w-4 h-4 rounded accent-blue-500" data-testid="check-visu-alarm-sound" />
                {{ $t('settings.visuAlarms.sound') }}
              </label>
            </fieldset>

            <!-- Geräte -->
            <fieldset class="flex flex-col gap-1.5">
              <legend class="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">{{ $t('settings.visuAlarms.devices') }}</legend>
              <p v-if="devices.length === 0 && missingDevices(alarm).length === 0" class="text-xs text-slate-500">{{ $t('settings.visuAlarms.noDevices') }}</p>
              <template v-else>
                <p class="text-xs text-slate-500">{{ $t('settings.visuAlarms.devicesHint') }}</p>
                <label v-for="device in devices" :key="device.slug" class="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    class="w-4 h-4 rounded accent-blue-500"
                    :checked="alarm.devices.includes(device.slug)"
                    :data-testid="`check-visu-alarm-device-${device.slug}`"
                    @change="toggleDevice(alarm, device.slug)"
                  />
                  {{ device.name }}
                </label>
                <!-- Profiles the alarm still names but that were renamed or deleted: shown ticked so they can be unticked. -->
                <label v-for="slug in missingDevices(alarm)" :key="`missing-${slug}`" class="flex items-center gap-2 text-sm cursor-pointer text-red-400">
                  <input
                    type="checkbox"
                    class="w-4 h-4 rounded accent-blue-500"
                    checked
                    :data-testid="`check-visu-alarm-device-missing-${slug}`"
                    @change="toggleDevice(alarm, slug)"
                  />
                  {{ $t('settings.visuAlarms.missingDevice', { slug }) }}
                </label>
              </template>
            </fieldset>

            <p v-if="rowError(alarm)" class="text-xs text-red-400" data-testid="visu-alarm-error">{{ $t(rowError(alarm)) }}</p>
          </div>
        </div>

        <div
          v-if="msg"
          :class="['p-3 rounded-lg text-sm border', msg.ok ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30']"
          data-testid="visu-alarms-msg"
        >{{ msg.text }}</div>

        <!-- After a failed load the list is incomplete: saving it would delete the stored alarms. -->
        <div v-if="!loading && !loadFailed" class="flex justify-end">
          <button type="button" class="btn-primary" :disabled="saving || hasErrors" data-testid="btn-save-visu-alarms" @click="save">
            <Spinner v-if="saving" size="sm" color="white" />
            {{ $t('common.save') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { dpApi, visuAlarmsApi, visuDevicesApi } from '@/api/client'
import DpCombobox from '@/components/ui/DpCombobox.vue'
import HelpButton from '@/components/ui/HelpButton.vue'
import IconPicker from '@/components/ui/IconPicker.vue'
import Spinner from '@/components/ui/Spinner.vue'
import VisuIcon from '@/components/ui/VisuIcon.vue'

// Mirrors AlarmOperator in obs/visu_alarms.py (the MESSAGE conditions without "any").
const OPERATORS = ['==', '!=', '<', '<=', '>', '>=', 'contains', 'contains not', 'starts with', 'ends with']

const { t } = useI18n()

const alarms = ref([])
const pages = ref([])
const devices = ref([])
const dpNames = ref({})
// Datapoints the alarms still name but that no longer exist (deleted meanwhile).
const missingDatapoints = ref(new Set())
const loading = ref(true)
const loadFailed = ref(false)
const saving = ref(false)
const msg = ref(null)

function compareText(value) {
  return value === null || value === undefined ? '' : String(value)
}

/** The text field's value back to what the API stores: the original value while the text is unchanged. */
function compareValue(row) {
  if (row.compare_value === compareText(row._compare)) return row._compare
  const text = row.compare_value.trim()
  if (text === '') return null
  if (text === 'true' || text === 'false') return text === 'true'
  if (/^-?\d+(\.\d+)?$/.test(text)) return Number(text)
  return row.compare_value
}

function toRow(alarm, open = false) {
  return {
    ...alarm,
    compare_value: compareText(alarm.compare_value),
    _compare: alarm.compare_value,
    page_id: alarm.page_id ?? '',
    devices: [...alarm.devices],
    _open: open,
  }
}

function newId() {
  return `alarm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
}

// Pages in tree order, labelled with their full path ("Haus › EG › Küche").
const pageOptions = computed(() => {
  const byParent = new Map()
  for (const node of pages.value) {
    if (!byParent.has(node.parent_id)) byParent.set(node.parent_id, [])
    byParent.get(node.parent_id).push(node)
  }
  const options = []
  const walk = (parentId, prefix) => {
    for (const node of byParent.get(parentId) ?? []) {
      const label = prefix ? `${prefix} › ${node.name}` : node.name
      options.push({ id: node.id, label })
      walk(node.id, label)
    }
  }
  walk(null, '')
  return options
})

const pageIds = computed(() => new Set(pages.value.map(node => node.id)))

function selectDatapoint(alarm, dp) {
  alarm.datapoint_id = dp.id
  dpNames.value = { ...dpNames.value, [dp.id]: dp.name }
}

function toggleDevice(alarm, slug) {
  alarm.devices = alarm.devices.includes(slug) ? alarm.devices.filter(s => s !== slug) : [...alarm.devices, slug]
}

const deviceSlugs = computed(() => new Set(devices.value.map(device => device.slug)))

/** Device profiles the alarm is restricted to that no longer exist (renamed or deleted). */
function missingDevices(alarm) {
  return alarm.devices.filter(slug => !deviceSlugs.value.has(slug))
}

function rowError(alarm) {
  if (!alarm.name.trim()) return 'settings.visuAlarms.errorName'
  if (!alarm.datapoint_id) return 'settings.visuAlarms.errorDatapoint'
  if (missingDatapoints.value.has(alarm.datapoint_id)) return 'settings.visuAlarms.errorMissingDatapoint'
  if (alarm.display === 'message' && !alarm.title.trim() && !alarm.text.trim()) return 'settings.visuAlarms.errorMessage'
  if (alarm.display === 'page' && !alarm.page_id) return 'settings.visuAlarms.errorPage'
  if (alarm.display === 'page' && !pageIds.value.has(alarm.page_id)) return 'settings.visuAlarms.errorMissingPage'
  if (!alarm.ack_button && !alarm.end_on_clear) return 'settings.visuAlarms.errorEnd'
  if (missingDevices(alarm).length > 0) return 'settings.visuAlarms.errorMissingDevice'
  return ''
}

const hasErrors = computed(() => alarms.value.some(alarm => rowError(alarm)))

function addAlarm() {
  alarms.value.push(toRow({
    id: newId(),
    name: '',
    enabled: true,
    datapoint_id: '',
    operator: '==',
    compare_value: true,
    display: 'message',
    title: '',
    text: '',
    icon: '',
    severity: 'info',
    page_id: '',
    ack_button: true,
    end_on_clear: false,
    sound: false,
    devices: [],
  }, true))
  msg.value = null
}

async function loadDatapointNames(ids) {
  const names = {}
  const missing = new Set(missingDatapoints.value)
  await Promise.all(ids.map(async (id) => {
    try {
      const { data } = await dpApi.get(id)
      names[id] = data.name
    } catch (err) {
      // 404 = deleted meanwhile: the alarm can never fire and PUT would reject the whole list.
      // Any other failure (network) only leaves the name unresolved.
      if (err?.response?.status === 404) {
        missing.add(id)
        names[id] = t('settings.visuAlarms.missingDatapoint')
      } else {
        names[id] = id
      }
    }
  }))
  missingDatapoints.value = missing
  dpNames.value = { ...dpNames.value, ...names }
}

async function save() {
  saving.value = true
  msg.value = null
  try {
    // eslint-disable-next-line no-unused-vars
    const payload = alarms.value.map(({ _open, _compare, ...alarm }) => ({
      ...alarm,
      compare_value: compareValue({ ...alarm, _compare }),
      page_id: alarm.page_id || null,
    }))
    const { data } = await visuAlarmsApi.update(payload)
    const open = new Set(alarms.value.filter(a => a._open).map(a => a.id))
    alarms.value = data.map(alarm => toRow(alarm, open.has(alarm.id)))
    msg.value = { ok: true, text: t('settings.visuAlarms.saved') }
  } catch (err) {
    const detail = err?.response?.data?.detail
    msg.value = { ok: false, text: typeof detail === 'string' ? detail : t('settings.visuAlarms.saveError') }
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const [alarmRes, pageRes, deviceRes] = await Promise.all([visuAlarmsApi.list(), visuDevicesApi.pages(), visuDevicesApi.list()])
    alarms.value = alarmRes.data.map(alarm => toRow(alarm))
    pages.value = pageRes.data
    devices.value = deviceRes.data
    await loadDatapointNames([...new Set(alarms.value.map(a => a.datapoint_id))])
  } catch {
    loadFailed.value = true
    msg.value = { ok: false, text: t('settings.visuAlarms.loadError') }
  } finally {
    loading.value = false
  }
})
</script>
