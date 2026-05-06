<template>
  <div class="flex flex-col gap-4">

    <!-- Historischer Versatz -->
    <div class="form-group">
      <label class="label">Historischer Versatz</label>
      <div class="flex gap-2">
        <select v-model="offsetSelect" class="input" @change="onOffsetSelectChange">
          <option value="1">1 Tag</option>
          <option value="7">7 Tage</option>
          <option value="14">14 Tage</option>
          <option value="custom">Andere (1–30 Tage) …</option>
        </select>
        <input
          v-if="offsetSelect === 'custom'"
          v-model.number="offsetCustom"
          type="number"
          min="1"
          max="30"
          class="input w-28"
          placeholder="Tage"
          @input="emitOffsetCustom"
        />
      </div>
      <p class="hint">Anzahl Tage in der Vergangenheit, deren Schaltzustände wiederholt werden.</p>
    </div>

    <!-- Steuerobjekt -->
    <div class="form-group">
      <label class="label">Steuerobjekt</label>
      <DpCombobox
        :model-value="modelValue.control_dp_id ?? ''"
        :display-name="controlDpName"
        placeholder="Objekt suchen (Boolean) …"
        @select="onControlDpSelect"
      />
      <p class="hint">
        Boolean-Datenpunkt: Wert 1 = Anwesend (Simulation aus), Wert 0 = Abwesend (Simulation an).
        Leer = Simulation immer aktiv.
      </p>
    </div>

    <!-- Steuerobjekt invertieren -->
    <div class="form-group">
      <label class="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          :checked="modelValue.control_invert ?? false"
          @change="emit('update:modelValue', { ...modelValue, control_invert: $event.target.checked })"
          class="w-4 h-4 rounded"
        />
        <span class="text-sm text-slate-600 dark:text-slate-300">Steuerobjekt invertieren</span>
      </label>
      <p class="hint">Bei Aktivierung gilt: Wert 0 = Anwesend (Simulation aus).</p>
    </div>

    <!-- Verhalten bei Anwesenheit -->
    <div class="form-group">
      <label class="label">Verhalten bei Anwesenheit</label>
      <select
        :value="modelValue.on_presence ?? 'behalten'"
        class="input"
        @change="emit('update:modelValue', { ...modelValue, on_presence: $event.target.value })"
      >
        <option value="behalten">Wert behalten</option>
        <option value="zuruecksetzen">Wert zurücksetzen (false / 0)</option>
        <option value="setzen">Wert setzen auf …</option>
      </select>
      <input
        v-if="(modelValue.on_presence ?? 'behalten') === 'setzen'"
        :value="modelValue.on_presence_value ?? ''"
        type="text"
        class="input mt-2"
        placeholder="z.B. 0 / 1 / false / true / 21.5"
        @input="emit('update:modelValue', { ...modelValue, on_presence_value: $event.target.value })"
      />
      <p class="hint">Was passiert mit den simulierten Objekten wenn Anwesenheit erkannt wird.</p>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { searchApi } from '@/api/client'
import DpCombobox from '@/components/ui/DpCombobox.vue'

const props = defineProps({
  modelValue: { type: Object, required: true },
})
const emit = defineEmits(['update:modelValue'])

// ── Versatz ──────────────────────────────────────────────────────────────────

const PRESETS = ['1', '7', '14']

function _currentOffsetDays() {
  return props.modelValue.offset_days ?? 7
}

const offsetSelect = ref(PRESETS.includes(String(_currentOffsetDays())) ? String(_currentOffsetDays()) : 'custom')
const offsetCustom = ref(_currentOffsetDays())

function onOffsetSelectChange() {
  if (offsetSelect.value !== 'custom') {
    emit('update:modelValue', { ...props.modelValue, offset_days: parseInt(offsetSelect.value) })
  } else {
    // Keep current custom value or default to 7
    const days = offsetCustom.value >= 1 && offsetCustom.value <= 30 ? offsetCustom.value : 7
    offsetCustom.value = days
    emit('update:modelValue', { ...props.modelValue, offset_days: days })
  }
}

function emitOffsetCustom() {
  const v = Math.min(30, Math.max(1, offsetCustom.value || 7))
  offsetCustom.value = v
  emit('update:modelValue', { ...props.modelValue, offset_days: v })
}

// Sync when parent changes model externally
watch(() => props.modelValue.offset_days, (days) => {
  if (days === undefined) return
  if (PRESETS.includes(String(days))) {
    offsetSelect.value = String(days)
  } else {
    offsetSelect.value = 'custom'
    offsetCustom.value = days
  }
}, { immediate: true })

// ── Steuerobjekt ─────────────────────────────────────────────────────────────

const controlDpName = ref('')

async function loadControlDpName(id) {
  if (!id) { controlDpName.value = ''; return }
  try {
    const { data } = await searchApi.search({ q: id, size: 1 })
    const item = data?.items?.find(i => i.id === id)
    controlDpName.value = item?.name ?? id
  } catch {
    controlDpName.value = id
  }
}

function onControlDpSelect(item) {
  if (!item) {
    emit('update:modelValue', { ...props.modelValue, control_dp_id: null })
    controlDpName.value = ''
  } else {
    emit('update:modelValue', { ...props.modelValue, control_dp_id: item.id })
    controlDpName.value = item.name
  }
}

watch(() => props.modelValue.control_dp_id, (id) => loadControlDpName(id), { immediate: true })
</script>
