<template>
  <div class="flex flex-col gap-4">
    <template v-for="(prop, key) in schema.properties" :key="key">

      <!-- Boolean: checkbox + inline label -->
      <div v-if="resolvedType(prop) === 'boolean'" class="flex items-center gap-2">
        <input
          type="checkbox"
          :id="`sf-${key}`"
          :checked="local[key]"
          class="w-4 h-4 rounded"
          :data-testid="`config-field-${key}`"
          @change="setBool(key, $event.target.checked)"
        />
        <label :for="`sf-${key}`" class="text-sm text-slate-600 dark:text-slate-300">
          {{ fieldLabel(key, prop) }}
        </label>
      </div>

      <!-- All other types: label on top -->
      <div v-else class="form-group">
        <label class="label">
          {{ fieldLabel(key, prop) }}
          <span v-if="isRequired(key)" class="text-red-400 ml-0.5">*</span>
        </label>

        <!-- Enum → select -->
        <select
          v-if="prop.enum"
          v-model="local[key]"
          class="input"
          :data-testid="`config-field-${key}`"
          @change="emitUpdate"
        >
          <option v-for="opt in prop.enum" :key="String(opt)" :value="opt">{{ opt }}</option>
        </select>

        <!-- Integer -->
        <input
          v-else-if="resolvedType(prop) === 'integer'"
          type="number"
          step="1"
          :value="local[key]"
          class="input"
          :data-testid="`config-field-${key}`"
          :placeholder="defaultPlaceholder(prop)"
          @change="setNumber(key, $event.target.value, true)"
        />

        <!-- Float/Number -->
        <input
          v-else-if="resolvedType(prop) === 'number'"
          type="number"
          step="any"
          :value="local[key]"
          class="input"
          :data-testid="`config-field-${key}`"
          :placeholder="defaultPlaceholder(prop)"
          @change="setNumber(key, $event.target.value, false)"
        />

        <!-- Password string -->
        <input
          v-else-if="prop.format === 'password'"
          type="password"
          :value="local[key] ?? ''"
          class="input"
          :data-testid="`config-field-${key}`"
          :placeholder="isOptional(prop) ? '(leer = nicht gesetzt)' : defaultPlaceholder(prop)"
          @input="setString(key, $event.target.value, isOptional(prop))"
        />

        <!-- String (including optional string | null) -->
        <input
          v-else
          type="text"
          :value="local[key] ?? ''"
          class="input"
          :data-testid="`config-field-${key}`"
          :placeholder="isOptional(prop) ? '(leer = nicht gesetzt)' : defaultPlaceholder(prop)"
          @input="setString(key, $event.target.value, isOptional(prop))"
        />

        <p v-if="prop.description" class="text-xs text-slate-400 mt-1">{{ prop.description }}</p>
      </div>

    </template>
  </div>
</template>

<script setup>
import { reactive, watch } from 'vue'

const props = defineProps({
  schema:     { type: Object, required: true },
  modelValue: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

// ── Helpers ───────────────────────────────────────────────────────────────

function resolvedType(prop) {
  if (!prop) return 'string'
  if (prop.type) return prop.type
  // anyOf: [{type: "string"}, {type: "null"}] → "string"
  if (prop.anyOf) {
    const nonNull = prop.anyOf.find(s => s.type !== 'null')
    return nonNull?.type ?? 'string'
  }
  return 'string'
}

function isOptional(prop) {
  return prop?.anyOf?.some(s => s.type === 'null') ?? false
}

function isRequired(key) {
  return (props.schema?.required ?? []).includes(key)
}

function fieldLabel(key, prop) {
  if (prop?.title) return prop.title
  return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function defaultPlaceholder(prop) {
  return prop?.default != null ? String(prop.default) : ''
}

// ── Local state ───────────────────────────────────────────────────────────

function buildLocal() {
  const result = {}
  for (const [key, prop] of Object.entries(props.schema?.properties ?? {})) {
    if (key in props.modelValue && props.modelValue[key] !== undefined) {
      result[key] = props.modelValue[key]
    } else if ('default' in prop) {
      result[key] = prop.default
    } else {
      result[key] = resolvedType(prop) === 'boolean' ? false : null
    }
  }
  return result
}

const local = reactive(buildLocal())

// Re-initialise when schema changes (adapter type switch in create form)
watch(() => props.schema, () => {
  const fresh = buildLocal()
  // Remove keys no longer in schema
  for (const k of Object.keys(local)) {
    if (!(k in fresh)) delete local[k]
  }
  Object.assign(local, fresh)
  emitUpdate()
}, { deep: true })

// Sync incoming modelValue changes (e.g. edit panel opened)
watch(() => props.modelValue, (val) => {
  for (const [k, v] of Object.entries(val)) {
    if (k in local && local[k] !== v) local[k] = v
  }
}, { deep: true })

// ── Emit ──────────────────────────────────────────────────────────────────

function emitUpdate() {
  const out = {}
  for (const [k, v] of Object.entries(local)) {
    const prop = props.schema?.properties?.[k]
    // Empty string for optional field → null
    out[k] = (prop && isOptional(prop) && v === '') ? null : v
  }
  emit('update:modelValue', out)
}

function setBool(key, value) {
  local[key] = value
  emitUpdate()
}

function setNumber(key, rawValue, integer) {
  if (rawValue === '' || rawValue === null) {
    local[key] = null
  } else {
    local[key] = integer ? parseInt(rawValue, 10) : parseFloat(rawValue)
  }
  emitUpdate()
}

function setString(key, value, optional) {
  local[key] = (optional && value === '') ? null : value
  emitUpdate()
}
</script>
