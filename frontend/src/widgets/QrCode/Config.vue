<script setup lang="ts">
import { reactive, watch } from 'vue'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive({
  content:         (props.modelValue.content         as string) ?? '',
  label:           (props.modelValue.label           as string) ?? '',
  errorCorrection: (props.modelValue.errorCorrection as string) ?? 'M',
  darkColor:       (props.modelValue.darkColor       as string) ?? '#000000',
  lightColor:      (props.modelValue.lightColor      as string) ?? '#ffffff',
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-3">

    <!-- Inhalt -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Inhalt (URL oder Text)</label>
      <textarea
        v-model="cfg.content"
        rows="3"
        placeholder="https://example.com"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500 resize-none"
      />
    </div>

    <!-- Bezeichnung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Bezeichnung (optional)</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. WiFi-Passwort"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Fehlerkorrektur -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Fehlerkorrektur</label>
      <select
        v-model="cfg.errorCorrection"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="L">L – niedrig (7 %)</option>
        <option value="M">M – mittel (15 %) – empfohlen</option>
        <option value="Q">Q – hoch (25 %)</option>
        <option value="H">H – sehr hoch (30 %)</option>
      </select>
    </div>

    <!-- Farben -->
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="block text-xs text-gray-400 mb-1">Vordergrundfarbe</label>
        <div class="flex items-center gap-2">
          <input
            v-model="cfg.darkColor"
            type="color"
            class="h-8 w-12 rounded border border-gray-700 bg-gray-800 cursor-pointer"
          />
          <input
            v-model="cfg.darkColor"
            type="text"
            maxlength="7"
            class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">Hintergrundfarbe</label>
        <div class="flex items-center gap-2">
          <input
            v-model="cfg.lightColor"
            type="color"
            class="h-8 w-12 rounded border border-gray-700 bg-gray-800 cursor-pointer"
          />
          <input
            v-model="cfg.lightColor"
            type="text"
            maxlength="7"
            class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
    </div>

  </div>
</template>
