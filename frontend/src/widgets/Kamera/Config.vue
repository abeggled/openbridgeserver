<script setup lang="ts">
import { reactive, watch, computed } from 'vue'

const props = defineProps<{
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, unknown>): void
}>()

const cfg = reactive({
  label:           (props.modelValue.label          as string)  ?? '',
  url:             (props.modelValue.url             as string)  ?? '',
  streamType:      (props.modelValue.streamType      as string)  ?? 'mjpeg',
  authType:        (props.modelValue.authType        as string)  ?? 'none',
  username:        (props.modelValue.username        as string)  ?? '',
  password:        (props.modelValue.password        as string)  ?? '',
  apiKeyParam:     (props.modelValue.apiKeyParam     as string)  ?? 'token',
  apiKeyValue:     (props.modelValue.apiKeyValue     as string)  ?? '',
  refreshInterval: (props.modelValue.refreshInterval as number)  ?? 5,
  aspectRatio:     (props.modelValue.aspectRatio     as string)  ?? '16/9',
  objectFit:       (props.modelValue.objectFit       as string)  ?? 'contain',
  useProxy:        (props.modelValue.useProxy        as boolean) ?? false,
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })

const showBasicAuth  = computed(() => cfg.authType === 'basic')
const showApiKeyAuth = computed(() => cfg.authType === 'apikey')
const showRefresh    = computed(() => cfg.streamType === 'snapshot')
</script>

<template>
  <div class="space-y-3">

    <!-- Label -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Bezeichnung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Eingang, Garten …"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Stream-Typ -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Stream-Typ</label>
      <select
        v-model="cfg.streamType"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="mjpeg">MJPEG (Motion JPEG)</option>
        <option value="snapshot">JPEG Snapshot (Auto-Refresh)</option>
        <option value="hls">HLS (.m3u8)</option>
      </select>
    </div>

    <!-- URL -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Stream-URL</label>
      <input
        v-model="cfg.url"
        type="text"
        placeholder="http://192.168.1.100/video.cgi"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Snapshot Refresh-Intervall -->
    <div v-if="showRefresh">
      <label class="block text-xs text-gray-400 mb-1">
        Aktualisierungsintervall (Sekunden)
      </label>
      <input
        v-model.number="cfg.refreshInterval"
        type="number"
        min="1"
        max="3600"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Authentifizierung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Authentifizierung</label>
      <select
        v-model="cfg.authType"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="none">Keine</option>
        <option value="basic">Basic Auth (Benutzername / Passwort)</option>
        <option value="apikey">API-Key (Query-Parameter)</option>
      </select>
    </div>

    <!-- Basic Auth -->
    <template v-if="showBasicAuth">
      <div class="grid grid-cols-2 gap-2">
        <div>
          <label class="block text-xs text-gray-400 mb-1">Benutzername</label>
          <input
            v-model="cfg.username"
            type="text"
            autocomplete="off"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Passwort</label>
          <input
            v-model="cfg.password"
            type="password"
            autocomplete="new-password"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
      <p class="text-xs text-yellow-600">
        Credentials werden in die URL eingebettet (http://user:pass@host/…).
        Nur für interne Netzwerke empfohlen.
      </p>
    </template>

    <!-- API Key -->
    <template v-if="showApiKeyAuth">
      <div class="grid grid-cols-2 gap-2">
        <div>
          <label class="block text-xs text-gray-400 mb-1">Parameter-Name</label>
          <input
            v-model="cfg.apiKeyParam"
            type="text"
            placeholder="token"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">API-Key</label>
          <input
            v-model="cfg.apiKeyValue"
            type="password"
            autocomplete="new-password"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
    </template>

    <!-- Proxy -->
    <div class="flex items-center gap-2">
      <input
        id="cam-proxy"
        v-model="cfg.useProxy"
        type="checkbox"
        class="rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
      />
      <label for="cam-proxy" class="text-xs text-gray-300 cursor-pointer">
        Via Server-Proxy laden
        <span class="text-gray-500 font-normal ml-1">(Mixed-Content / HTTPS → HTTP)</span>
      </label>
    </div>

    <!-- Darstellung -->
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="block text-xs text-gray-400 mb-1">Seitenverhältnis</label>
        <select
          v-model="cfg.aspectRatio"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="16/9">16:9</option>
          <option value="4/3">4:3</option>
          <option value="1/1">1:1 (Quadrat)</option>
          <option value="free">Frei (Widget-Grösse)</option>
        </select>
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">Bildanpassung</label>
        <select
          v-model="cfg.objectFit"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="contain">Einpassen (contain)</option>
          <option value="cover">Füllen (cover)</option>
          <option value="fill">Strecken (fill)</option>
        </select>
      </div>
    </div>

  </div>
</template>
