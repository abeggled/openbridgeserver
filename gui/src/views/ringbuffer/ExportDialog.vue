<template>
  <Modal v-model="openModel" title="Export" :soft-backdrop="true" max-width="lg">
    <div class="flex flex-col gap-4">
      <!-- Delimiter / Quote / Escape -->
      <div class="form-group">
        <div class="flex items-center justify-between mb-1">
          <label class="label">CSV-Format</label>
          <button
            type="button"
            class="btn-secondary btn-sm"
            data-testid="export-reset-rfc4180"
            title="Setzt Trennzeichen, Anführungszeichen und Escape-Zeichen auf die RFC-4180-Vorgabe."
            @click="resetToRfc4180"
          >
            ↺ RFC 4180
          </button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div class="flex flex-col gap-1">
            <label class="text-xs text-slate-500">Trennzeichen</label>
            <div class="flex items-center gap-2">
              <input
                v-model="form.delimiter"
                type="text"
                maxlength="1"
                class="input font-mono w-16 text-center"
                data-testid="export-delimiter"
                :placeholder="form.delimiter === '\t' ? '⇥' : ','"
              />
              <button
                type="button"
                class="btn-secondary btn-sm"
                data-testid="export-delimiter-tab"
                title="Tabulator als Trennzeichen setzen (TSV)"
                @click="form.delimiter = '\t'"
              >
                ⇥ Tab
              </button>
              <span v-if="form.delimiter === '\t'" class="text-xs text-slate-500">Tabulator</span>
            </div>
          </div>
          <div class="flex flex-col gap-1">
            <label class="text-xs text-slate-500">Anführungszeichen</label>
            <input
              v-model="form.quote_char"
              type="text"
              maxlength="1"
              class="input font-mono w-16 text-center"
              data-testid="export-quote-char"
              placeholder='"'
            />
          </div>
          <div class="flex flex-col gap-1">
            <label class="text-xs text-slate-500">Escape-Zeichen</label>
            <input
              v-model="form.escape_char"
              type="text"
              maxlength="1"
              class="input font-mono w-16 text-center"
              data-testid="export-escape-char"
              placeholder="(leer)"
            />
          </div>
        </div>
        <p class="text-xs text-slate-500 mt-1.5">
          Dateiendung: <code class="font-mono">.{{ form.delimiter === '\t' ? 'tsv' : 'csv' }}</code>.
          Escape-Zeichen leer = RFC 4180 (Anführungszeichen im Feld werden verdoppelt).
        </p>
      </div>

      <!-- Encoding -->
      <div class="form-group">
        <label class="label">Zeichenkodierung</label>
        <select v-model="form.encoding" class="input" data-testid="export-encoding">
          <option value="utf8">UTF-8</option>
          <option value="utf8-bom">UTF-8 mit BOM (für ältere Excel-Versionen)</option>
        </select>
      </div>

      <!-- Optional columns -->
      <div class="form-group">
        <label class="label">Zusätzliche Spalten</label>
        <div class="flex flex-col gap-1.5">
          <label class="inline-flex items-center gap-2 text-sm">
            <input type="checkbox" v-model="form.include_unit" data-testid="export-include-unit" />
            <span><code class="font-mono">unit</code> — Einheit aus dem Datapoint</span>
          </label>
          <label class="inline-flex items-center gap-2 text-sm">
            <input type="checkbox" v-model="form.include_matched_set_ids" data-testid="export-include-matched" />
            <span><code class="font-mono">matched_set_ids</code> — IDs der Sets, die jede Zeile getroffen haben</span>
          </label>
        </div>
      </div>

      <div
        v-if="pendingRowCount !== null"
        class="rounded-md border border-amber-400 bg-amber-50 p-3 text-sm text-amber-900"
        data-testid="export-warning"
      >
        Dieser Export würde <strong>{{ pendingRowCount.toLocaleString('de-DE') }}</strong> Zeilen erzeugen.
        Trotzdem fortfahren?
      </div>

      <p v-if="errorMsg" class="text-sm text-red-500" data-testid="export-error">{{ errorMsg }}</p>
    </div>

    <template #footer>
      <button class="btn-secondary" data-testid="btn-export-cancel" @click="openModel = false">Abbrechen</button>
      <button class="btn-primary" :disabled="busy || !formValid" data-testid="btn-export-go" @click="onExport">
        <Spinner v-if="busy" size="sm" color="white" />
        {{ pendingRowCount !== null ? 'Trotzdem exportieren' : 'Exportieren' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { ringbufferApi } from '@/api/client'
import Modal from '@/components/ui/Modal.vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  setIds: { type: Array, default: () => [] },
  time: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue'])

const openModel = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const EXPORT_WARN_THRESHOLD = 1000

// RFC 4180 defaults: comma delimiter, double-quote quoting, no separate
// escape (quote-doubling inside quoted fields handles internal quotes).
const RFC_4180_DEFAULTS = Object.freeze({
  delimiter: ',',
  quote_char: '"',
  escape_char: '',
})

const form = reactive({
  delimiter: ',',
  quote_char: '"',
  escape_char: '',
  encoding: 'utf8',
  include_unit: true,
  include_matched_set_ids: false,
})
const busy = ref(false)
const errorMsg = ref('')
const pendingRowCount = ref(null)

// delimiter and quote_char are required single characters. escape_char may be
// empty (= no escape, RFC 4180 quote-doubling). Both backend Pydantic and the
// UI enforce this so the Export button is disabled while the form is invalid.
const formValid = computed(
  () => form.delimiter.length === 1 && form.quote_char.length === 1 && form.escape_char.length <= 1,
)

function resetToRfc4180() {
  Object.assign(form, RFC_4180_DEFAULTS)
}

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) {
      pendingRowCount.value = null
      return
    }
    errorMsg.value = ''
    pendingRowCount.value = null
    try {
      const { data } = await ringbufferApi.getExportSettings()
      if (data) Object.assign(form, data)
    } catch {
      // fall back to defaults silently
    }
  },
  { immediate: true },
)

// Any change to the selection invalidates a pending confirmation — the row
// count we asked the user about no longer matches what we'd export.
//
// We have to be careful here: the parent (RingBufferView) re-renders on every
// live WS entry, and the bindings `:set-ids="activeTopbarSetIds()"` and
// `:time="timeFilterToPayload(timeFilter)"` return *new* array/object refs
// each render even when their content is unchanged. Vue 3's `deep: true`
// affects dep-tracking traversal but `hasChanged` still falls back to
// Object.is on the top-level snapshot — so a naive `watch(() => [setIds,
// time], …)` fires on every parent re-render and wipes pendingRowCount
// before the user can confirm. We fold both inputs into a content
// fingerprint string and watch that instead — string identity is stable
// across re-renders when the content is.
const filterFingerprint = computed(() => {
  const ids = Array.isArray(props.setIds) ? props.setIds.slice().sort().join('|') : ''
  const time = props.time ? JSON.stringify(props.time) : ''
  return `${ids}#${time}`
})
watch(filterFingerprint, () => {
  pendingRowCount.value = null
})

async function onExport() {
  if (busy.value) return
  busy.value = true
  errorMsg.value = ''
  try {
    if (pendingRowCount.value === null) {
      const countResp = await ringbufferApi.countExportRows({
        set_ids: props.setIds || [],
        time: props.time || null,
      })
      const rowCount = countResp?.data?.row_count ?? 0
      if (rowCount > EXPORT_WARN_THRESHOLD) {
        pendingRowCount.value = rowCount
        return
      }
    }

    // Persist settings in the background; failure doesn't block the export
    ringbufferApi.putExportSettings({ ...form }).catch(() => {})

    const body = {
      set_ids: props.setIds || [],
      time: props.time || null,
      delimiter: form.delimiter,
      quote_char: form.quote_char,
      escape_char: form.escape_char,
      encoding: form.encoding,
      include_unit: form.include_unit,
      include_matched_set_ids: form.include_matched_set_ids,
    }
    const resp = await ringbufferApi.exportMultiCsv(body)

    // Trigger browser download
    const blob = resp.data instanceof Blob ? resp.data : new Blob([resp.data])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    // Extract filename from Content-Disposition if present
    const cd = resp.headers?.['content-disposition'] || ''
    const match = cd.match(/filename="([^"]+)"/)
    const fallbackExt = form.delimiter === '\t' ? 'tsv' : 'csv'
    a.download = match ? match[1] : `ringbuffer_export.${fallbackExt}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    pendingRowCount.value = null
    openModel.value = false
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || 'Export fehlgeschlagen'
  } finally {
    busy.value = false
  }
}
</script>
