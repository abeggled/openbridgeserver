<template>
  <form @submit.prevent="submit" class="flex flex-col gap-4">
    <div class="grid grid-cols-2 gap-4">
      <div class="form-group">
        <label class="label">Adapter *</label>
        <select v-model="form.adapter_type" class="input" required>
          <option value="">Adapter wählen …</option>
          <option v-for="a in adapterTypes" :key="a" :value="a">{{ a }}</option>
        </select>
      </div>
      <div class="form-group">
        <label class="label">Direction *</label>
        <select v-model="form.direction" class="input">
          <option value="SOURCE">SOURCE — Adapter → System</option>
          <option value="DEST">DEST — System → Adapter</option>
          <option value="BOTH">BOTH — beidseitig</option>
        </select>
      </div>
    </div>

    <!-- Binding config (JSON editor) -->
    <div class="form-group">
      <label class="label">
        Binding Config (JSON)
        <button type="button" v-if="schema" @click="showSchema = !showSchema" class="ml-2 text-xs text-blue-400 hover:underline">
          {{ showSchema ? 'Schema ausblenden' : 'Schema anzeigen' }}
        </button>
      </label>
      <div v-if="showSchema && schema" class="mb-2 p-3 bg-surface-700 rounded-lg text-xs font-mono text-slate-400 max-h-40 overflow-y-auto">
        <pre>{{ JSON.stringify(schema?.properties ?? {}, null, 2) }}</pre>
      </div>
      <textarea v-model="configJson" class="input font-mono text-sm resize-none h-32" placeholder='{"group_address": "1/2/3", "dpt": "DPT9"}' />
    </div>

    <div class="flex items-center gap-2">
      <input type="checkbox" id="enabled" v-model="form.enabled" class="w-4 h-4 rounded" />
      <label for="enabled" class="text-sm text-slate-300">Aktiviert</label>
    </div>

    <div v-if="error" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">{{ error }}</div>

    <div class="flex justify-end gap-3 pt-2">
      <button type="button" @click="$emit('cancel')" class="btn-secondary">Abbrechen</button>
      <button type="submit" class="btn-primary" :disabled="saving">
        <Spinner v-if="saving" size="sm" color="white" />
        Speichern
      </button>
    </div>
  </form>
</template>

<script setup>
import { ref, reactive, watch, onMounted } from 'vue'
import { dpApi, adapterApi } from '@/api/client'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  dpId:    { type: String, required: true },
  initial: { type: Object, default: null },
})
const emit = defineEmits(['save', 'cancel'])

const saving      = ref(false)
const error       = ref(null)
const showSchema  = ref(false)
const schema      = ref(null)
const adapterTypes = ref([])
const configJson  = ref('{}')

const form = reactive({
  adapter_type: '',
  direction:    'SOURCE',
  config:       {},
  enabled:      true,
})

watch(() => props.initial, val => {
  if (val) {
    form.adapter_type = val.adapter_type
    form.direction    = val.direction
    form.enabled      = val.enabled
    configJson.value  = JSON.stringify(val.config, null, 2)
  }
}, { immediate: true })

watch(() => form.adapter_type, async (type) => {
  if (!type) return
  try { const { data } = await adapterApi.bindingSchema(type); schema.value = data } catch {}
})

onMounted(async () => {
  try {
    const { data } = await adapterApi.list()
    adapterTypes.value = data.map(a => a.adapter_type)
  } catch {}
})

async function submit() {
  error.value  = null
  saving.value = true
  try {
    let cfg
    try { cfg = JSON.parse(configJson.value) } catch {
      error.value = 'Ungültiges JSON in Binding Config'; saving.value = false; return
    }
    if (props.initial) {
      await dpApi.updateBinding(props.dpId, props.initial.id, {
        direction: form.direction, config: cfg, enabled: form.enabled,
      })
    } else {
      await dpApi.createBinding(props.dpId, {
        adapter_type: form.adapter_type, direction: form.direction, config: cfg, enabled: form.enabled,
      })
    }
    emit('save')
  } catch (e) {
    error.value = e.response?.data?.detail ?? 'Fehler beim Speichern'
  } finally {
    saving.value = false
  }
}
</script>
