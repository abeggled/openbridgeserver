<template>
  <form @submit.prevent="submit" class="flex flex-col gap-4">
    <div class="form-group">
      <label class="label">Name *</label>
      <input v-model="form.name" type="text" class="input" placeholder="z.B. Wohnzimmer Temperatur" required />
    </div>

    <div class="grid grid-cols-2 gap-4">
      <div class="form-group">
        <label class="label">Datentyp *</label>
        <select v-model="form.data_type" class="input" required>
          <option v-for="dt in datatypes" :key="dt.name" :value="dt.name">{{ dt.name }}</option>
        </select>
      </div>
      <div class="form-group">
        <label class="label">Einheit</label>
        <input v-model="form.unit" type="text" class="input" placeholder="°C, %, lx …" />
      </div>
    </div>

    <div class="form-group">
      <label class="label">Tags <span class="text-slate-500 font-normal">(kommagetrennt)</span></label>
      <input v-model="tagsInput" type="text" class="input" placeholder="heizung, eg, wohnzimmer" />
    </div>

    <div class="form-group">
      <label class="label">MQTT Alias <span class="text-slate-500 font-normal">(optional)</span></label>
      <input v-model="form.mqtt_alias" type="text" class="input" placeholder="alias/eg/wohnzimmer/temperatur/value" />
    </div>

    <div v-if="error" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">{{ error }}</div>

    <div class="flex justify-end gap-3 pt-2">
      <button type="button" @click="$emit('cancel')" class="btn-secondary">Abbrechen</button>
      <button type="submit" class="btn-primary" :disabled="saving">
        <Spinner v-if="saving" size="sm" color="white" />
        {{ saving ? 'Speichern …' : 'Speichern' }}
      </button>
    </div>
  </form>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  initial:   { type: Object, default: null },
  datatypes: { type: Array, default: () => [] },
})
const emit = defineEmits(['save', 'cancel'])

const saving    = ref(false)
const error     = ref(null)
const tagsInput = ref('')

const form = reactive({
  name:       '',
  data_type:  'FLOAT',
  unit:       '',
  tags:       [],
  mqtt_alias: '',
})

watch(() => props.initial, (val) => {
  if (val) {
    form.name       = val.name
    form.data_type  = val.data_type
    form.unit       = val.unit ?? ''
    form.tags       = val.tags ?? []
    form.mqtt_alias = val.mqtt_alias ?? ''
    tagsInput.value = val.tags?.join(', ') ?? ''
  }
}, { immediate: true })

async function submit() {
  error.value  = null
  saving.value = true
  try {
    const tags = tagsInput.value.split(',').map(t => t.trim()).filter(Boolean)
    await emit('save', {
      name:       form.name,
      data_type:  form.data_type,
      unit:       form.unit || null,
      tags,
      mqtt_alias: form.mqtt_alias || null,
    })
  } catch (e) {
    error.value = e.response?.data?.detail ?? 'Fehler beim Speichern'
    saving.value = false
  }
}
</script>
