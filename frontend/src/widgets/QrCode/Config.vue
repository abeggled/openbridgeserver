<script setup lang="ts">
import { reactive, watch } from 'vue'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive({
  qrType:          (props.modelValue.qrType          as string)  ?? 'url',
  label:           (props.modelValue.label           as string)  ?? '',
  errorCorrection: (props.modelValue.errorCorrection as string)  ?? 'M',
  darkColor:       (props.modelValue.darkColor       as string)  ?? '#000000',
  lightColor:      (props.modelValue.lightColor      as string)  ?? '#ffffff',
  // URL
  url_url:         (props.modelValue.url_url         as string)  ?? '',
  // WiFi
  wifi_ssid:       (props.modelValue.wifi_ssid       as string)  ?? '',
  wifi_password:   (props.modelValue.wifi_password   as string)  ?? '',
  wifi_encryption: (props.modelValue.wifi_encryption as string)  ?? 'WPA',
  wifi_hidden:     (props.modelValue.wifi_hidden     as boolean) ?? false,
  // vCard
  vcard_firstname: (props.modelValue.vcard_firstname as string)  ?? '',
  vcard_lastname:  (props.modelValue.vcard_lastname  as string)  ?? '',
  vcard_company:   (props.modelValue.vcard_company   as string)  ?? '',
  vcard_mobile:    (props.modelValue.vcard_mobile    as string)  ?? '',
  vcard_email:     (props.modelValue.vcard_email     as string)  ?? '',
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })

const inputClass = 'w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500'
const selectClass = inputClass
</script>

<template>
  <div class="space-y-3">

    <!-- Bezeichnung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.labelAbove') }}</label>
      <input v-model="cfg.label" type="text" placeholder="z.B. Gäste-WLAN" :class="inputClass" />
    </div>

    <!-- Typ -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.type') }}</label>
      <select v-model="cfg.qrType" :class="selectClass">
        <option value="url">{{ $t('widgets.qrcode.typeUrl') }}</option>
        <option value="wifi">{{ $t('widgets.qrcode.typeWifi') }}</option>
        <option value="vcard">{{ $t('widgets.qrcode.typeVcard') }}</option>
      </select>
    </div>

    <!-- ── URL ───────────────────────────────────────────────────────────── -->
    <template v-if="cfg.qrType === 'url'">
      <div>
        <label class="block text-xs text-gray-400 mb-1">URL</label>
        <input
          v-model="cfg.url_url"
          type="text"
          placeholder="https://example.com"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
        />
      </div>
    </template>

    <!-- ── WiFi ──────────────────────────────────────────────────────────── -->
    <template v-else-if="cfg.qrType === 'wifi'">
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.encryption') }}</label>
        <select v-model="cfg.wifi_encryption" :class="selectClass">
          <option value="WPA">{{ $t('widgets.qrcode.encWpa') }}</option>
          <option value="WEP">{{ $t('widgets.qrcode.encWep') }}</option>
          <option value="none">{{ $t('widgets.qrcode.encNone') }}</option>
        </select>
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.ssid') }}</label>
        <input v-model="cfg.wifi_ssid" type="text" placeholder="MeinHeimnetz" :class="inputClass" />
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.password') }}</label>
        <input v-model="cfg.wifi_password" type="text" placeholder="Passwort" :class="inputClass" />
      </div>
      <div class="flex items-center gap-2">
        <input
          id="wifi-hidden"
          v-model="cfg.wifi_hidden"
          type="checkbox"
          class="rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500"
        />
        <label for="wifi-hidden" class="text-xs text-gray-300 cursor-pointer">{{ $t('widgets.qrcode.hiddenSsid') }}</label>
      </div>
    </template>

    <!-- ── vCard ─────────────────────────────────────────────────────────── -->
    <template v-else-if="cfg.qrType === 'vcard'">
      <div class="grid grid-cols-2 gap-2">
        <div>
          <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.firstname') }}</label>
          <input v-model="cfg.vcard_firstname" type="text" placeholder="Max" :class="inputClass" />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.lastname') }}</label>
          <input v-model="cfg.vcard_lastname" type="text" placeholder="Mustermann" :class="inputClass" />
        </div>
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.company') }}</label>
        <input v-model="cfg.vcard_company" type="text" placeholder="Musterfirma AG" :class="inputClass" />
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.phone') }}</label>
        <input v-model="cfg.vcard_mobile" type="tel" placeholder="+41 79 123 45 67" :class="inputClass" />
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.email') }}</label>
        <input v-model="cfg.vcard_email" type="email" placeholder="max@musterfirma.ch" :class="inputClass" />
      </div>
    </template>

    <!-- ── Darstellung ───────────────────────────────────────────────────── -->
    <hr class="border-gray-700" />

    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.errorCorrection') }}</label>
      <select v-model="cfg.errorCorrection" :class="selectClass">
        <option value="L">{{ $t('widgets.qrcode.ecL') }}</option>
        <option value="M">{{ $t('widgets.qrcode.ecM') }}</option>
        <option value="Q">{{ $t('widgets.qrcode.ecQ') }}</option>
        <option value="H">{{ $t('widgets.qrcode.ecH') }}</option>
      </select>
    </div>

    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.fgColor') }}</label>
        <div class="flex items-center gap-2">
          <input v-model="cfg.darkColor" type="color" class="h-8 w-12 rounded border border-gray-700 bg-gray-800 cursor-pointer" />
          <input v-model="cfg.darkColor" type="text" maxlength="7" class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500" />
        </div>
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.qrcode.bgColor') }}</label>
        <div class="flex items-center gap-2">
          <input v-model="cfg.lightColor" type="color" class="h-8 w-12 rounded border border-gray-700 bg-gray-800 cursor-pointer" />
          <input v-model="cfg.lightColor" type="text" maxlength="7" class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500" />
        </div>
      </div>
    </div>

  </div>
</template>
