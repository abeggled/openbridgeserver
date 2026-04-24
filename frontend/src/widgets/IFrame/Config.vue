<script setup lang="ts">
import { reactive, watch, computed } from 'vue'

const props = defineProps<{
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, unknown>): void
}>()

const cfg = reactive({
  label:           (props.modelValue.label           as string)  ?? '',
  url:             (props.modelValue.url             as string)  ?? '',
  sandbox:         (props.modelValue.sandbox         as string)  ?? 'allow-same-origin allow-scripts allow-popups allow-forms',
  allowFullscreen: (props.modelValue.allowFullscreen as boolean) ?? false,
  aspectRatio:     (props.modelValue.aspectRatio     as string)  ?? '16/9',
})

watch(() => props.modelValue, (v) => {
  cfg.label           = (v.label           as string)  ?? ''
  cfg.url             = (v.url             as string)  ?? ''
  cfg.sandbox         = (v.sandbox         as string)  ?? 'allow-same-origin allow-scripts allow-popups allow-forms'
  cfg.allowFullscreen = (v.allowFullscreen as boolean) ?? false
  cfg.aspectRatio     = (v.aspectRatio     as string)  ?? '16/9'
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })

const SANDBOX_OPTIONS = [
  { key: 'allow-same-origin',              label: 'Same-Origin (Cookies, localStorage)' },
  { key: 'allow-scripts',                  label: 'Skripte (JavaScript)' },
  { key: 'allow-popups',                   label: 'Popups & Links' },
  { key: 'allow-forms',                    label: 'Formulare' },
  { key: 'allow-popups-to-escape-sandbox', label: 'Popups aus Sandbox entlassen' },
  { key: 'allow-top-navigation-by-user-activation', label: 'Navigation (nur bei Nutzerinteraktion)' },
]

function hasSandbox(key: string): boolean {
  return cfg.sandbox.split(' ').includes(key)
}

function toggleSandbox(key: string) {
  const parts = cfg.sandbox ? cfg.sandbox.split(' ').filter(Boolean) : []
  const idx = parts.indexOf(key)
  if (idx >= 0) parts.splice(idx, 1)
  else parts.push(key)
  cfg.sandbox = parts.join(' ')
}

const ASPECT_RATIOS = [
  { value: '16/9', label: '16:9' },
  { value: '4/3',  label: '4:3'  },
  { value: '1/1',  label: '1:1'  },
  { value: 'free', label: 'Frei (Höhe füllen)' },
]

const isValidUrl = computed(() => {
  if (!cfg.url) return true
  try { new URL(cfg.url); return true } catch { return false }
})
</script>

<template>
  <div class="space-y-3">

    <!-- Bezeichnung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Bezeichnung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Wetterkarte, Kalender …"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- URL -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">URL</label>
      <input
        v-model="cfg.url"
        type="url"
        placeholder="https://example.com"
        class="w-full bg-gray-800 border rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        :class="isValidUrl ? 'border-gray-700' : 'border-red-500'"
      />
      <p v-if="!isValidUrl" class="mt-1 text-xs text-red-400">Ungültige URL</p>
    </div>

    <!-- Seitenverhältnis -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Seitenverhältnis</label>
      <select
        v-model="cfg.aspectRatio"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option v-for="ar in ASPECT_RATIOS" :key="ar.value" :value="ar.value">{{ ar.label }}</option>
      </select>
    </div>

    <!-- Sandbox-Berechtigungen -->
    <div>
      <label class="block text-xs text-gray-400 mb-2">Sandbox-Berechtigungen</label>
      <div class="space-y-1.5">
        <label
          v-for="opt in SANDBOX_OPTIONS"
          :key="opt.key"
          class="flex items-center gap-2 cursor-pointer"
        >
          <input
            type="checkbox"
            :checked="hasSandbox(opt.key)"
            class="accent-blue-500"
            @change="toggleSandbox(opt.key)"
          />
          <span class="text-xs text-gray-300">{{ opt.label }}</span>
        </label>
      </div>
    </div>

    <!-- Vollbild -->
    <div>
      <label class="flex items-center gap-2 cursor-pointer">
        <input
          v-model="cfg.allowFullscreen"
          type="checkbox"
          class="accent-blue-500"
        />
        <span class="text-xs text-gray-300">Vollbildmodus erlauben</span>
      </label>
    </div>

    <!-- Sicherheitshinweis -->
    <div class="rounded bg-yellow-900/30 border border-yellow-700/50 px-3 py-2">
      <p class="text-xs text-yellow-300">
        Nur vertrauenswürdige URLs einbetten. Sandbox-Berechtigungen erhöhen den Funktionsumfang,
        jedoch auch das Sicherheitsrisiko.
      </p>
    </div>

  </div>
</template>
