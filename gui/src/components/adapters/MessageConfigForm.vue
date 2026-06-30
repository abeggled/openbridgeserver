<template>
  <div class="flex flex-col gap-4">
    <div v-for="provider in PROVIDERS" :key="provider.key" class="border border-slate-200 dark:border-slate-700 rounded-lg p-3">
      <div class="flex items-center justify-between gap-3">
        <label class="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-200">
          <input type="checkbox" :checked="providerConfig(provider.key).enabled" class="w-4 h-4 rounded" @change="setProvider(provider.key, { enabled: $event.target.checked })" />
          {{ provider.label }}
        </label>
        <button v-if="providerConfig(provider.key).enabled" type="button" class="btn-secondary btn-sm" @click="addTarget(provider.key)">
          {{ $t('adapters.message.addTarget') }}
        </button>
      </div>

      <div v-if="providerConfig(provider.key).enabled" class="mt-3 flex flex-col gap-3">
        <div class="grid grid-cols-2 gap-3">
          <div v-for="field in provider.fields" :key="field.key" class="form-group">
            <label class="label">{{ field.label }}</label>
            <input
              :type="field.secret ? 'password' : 'text'"
              :value="providerConfig(provider.key)[field.key] ?? ''"
              class="input"
              @input="setProvider(provider.key, { [field.key]: $event.target.value })"
            />
          </div>
        </div>

        <div class="flex flex-col gap-2">
          <div
            v-for="target in targetEntries(provider.key)"
            :key="target.name"
            class="grid gap-2 items-end"
            :class="provider.key === 'seven.io' ? 'grid-cols-[1fr_1.3fr_0.8fr_auto]' : 'grid-cols-[1fr_1.5fr_auto]'"
          >
            <div class="form-group">
              <label class="label">{{ $t('adapters.message.targetName') }}</label>
              <input :value="target.name" class="input" @change="renameTarget(provider.key, target.name, $event.target.value)" />
            </div>
            <div class="form-group">
              <label class="label">{{ targetLabel(provider.key) }}</label>
              <input
                :type="targetSecret(provider.key) ? 'password' : 'text'"
                :value="targetValue(provider.key, target.config)"
                class="input"
                @input="setTargetValue(provider.key, target.name, $event.target.value)"
              />
            </div>
            <div v-if="provider.key === 'seven.io'" class="form-group">
              <label class="label">{{ $t('adapters.message.channel') }}</label>
              <select :value="target.config.channel ?? 'sms'" class="input" @change="setTarget(provider.key, target.name, { channel: $event.target.value })">
                <option value="sms">SMS</option>
                <option value="voice">Voice</option>
              </select>
            </div>
            <button type="button" class="btn-danger btn-sm mb-0.5" @click="removeTarget(provider.key, target.name)">
              {{ $t('common.delete') }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  modelValue: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])
const { t } = useI18n()

const PROVIDERS = computed(() => [
  { key: 'pushover', label: 'Pushover', fields: [{ key: 'api_token', label: t('adapters.message.apiToken'), secret: true }] },
  { key: 'telegram', label: 'Telegram', fields: [{ key: 'bot_token', label: t('adapters.message.botToken'), secret: true }] },
  {
    key: 'seven.io',
    label: 'seven.io',
    fields: [
      { key: 'api_key', label: t('adapters.message.apiKey'), secret: true },
      { key: 'sender', label: t('adapters.message.sender'), secret: false },
    ],
  },
])

function cloneConfig() {
  return {
    ...props.modelValue,
    providers: { ...(props.modelValue.providers ?? {}) },
  }
}

function providerConfig(provider) {
  return props.modelValue.providers?.[provider] ?? { enabled: false, targets: {} }
}

function setProvider(provider, patch) {
  const next = cloneConfig()
  next.providers[provider] = {
    enabled: false,
    targets: {},
    ...(next.providers[provider] ?? {}),
    ...patch,
  }
  emit('update:modelValue', next)
}

function targetEntries(provider) {
  const targets = providerConfig(provider).targets ?? {}
  return Object.entries(targets).map(([name, config]) => ({ name, config: config ?? {} }))
}

function targetLabel(provider) {
  if (provider === 'pushover') return t('adapters.message.userKey')
  if (provider === 'telegram') return t('adapters.message.chatId')
  if (provider === 'seven.io') return t('adapters.message.to')
  return t('adapters.message.recipient')
}

function targetSecret(provider) {
  return provider === 'pushover'
}

function targetValue(provider, config) {
  if (provider === 'pushover') return config.user_key ?? ''
  if (provider === 'telegram') return config.chat_id ?? ''
  if (provider === 'seven.io') return config.to ?? ''
  return config.recipient ?? ''
}

function targetValuePatch(provider, value) {
  if (provider === 'pushover') return { user_key: value }
  if (provider === 'telegram') return { chat_id: value }
  if (provider === 'seven.io') return { to: value }
  return { recipient: value }
}

function setTarget(provider, target, patch) {
  const cfg = providerConfig(provider)
  setProvider(provider, {
    targets: {
      ...(cfg.targets ?? {}),
      [target]: {
        ...((cfg.targets ?? {})[target] ?? {}),
        ...patch,
      },
    },
  })
}

function setTargetValue(provider, target, value) {
  setTarget(provider, target, targetValuePatch(provider, value))
}

function addTarget(provider) {
  const cfg = providerConfig(provider)
  let name = 'default'
  let n = 2
  while ((cfg.targets ?? {})[name]) {
    name = `target_${n}`
    n += 1
  }
  const defaults = provider === 'seven.io' ? { channel: 'sms' } : {}
  setTarget(provider, name, defaults)
}

function renameTarget(provider, oldName, rawName) {
  const newName = String(rawName || '').trim()
  if (!newName || newName === oldName) return
  const cfg = providerConfig(provider)
  const targets = { ...(cfg.targets ?? {}) }
  if (targets[newName]) return
  targets[newName] = targets[oldName]
  delete targets[oldName]
  setProvider(provider, { targets })
}

function removeTarget(provider, target) {
  const cfg = providerConfig(provider)
  const targets = { ...(cfg.targets ?? {}) }
  delete targets[target]
  setProvider(provider, { targets })
}
</script>
