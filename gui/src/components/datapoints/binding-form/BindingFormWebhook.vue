<template>
  <div class="flex items-center justify-between">
    <div class="section-header grow">{{ $t('adapters.bindingForm.webhookSection') }}</div>
    <HelpButton help-id="adapters-webhook" />
  </div>

  <p class="hint">{{ $t('adapters.bindingForm.webhookIntro') }}</p>

  <!-- Slug -->
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.webhookSlugLabel') }}</label>
    <input
      v-model="cfg.slug"
      class="input font-mono text-sm"
      :placeholder="$t('adapters.bindingForm.webhookSlugPlaceholder')"
      required
      data-testid="webhook-slug"
    />
    <p class="hint">{{ $t('adapters.bindingForm.webhookSlugHint') }}</p>
  </div>

  <!-- Methods -->
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.webhookMethodsLabel') }}</label>
    <div class="flex gap-4">
      <label v-for="method in METHODS" :key="method" class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
        <input
          type="checkbox"
          class="w-4 h-4 rounded"
          :checked="selectedMethods.includes(method)"
          :data-testid="`webhook-method-${method}`"
          @change="toggleMethod(method)"
        />
        <code>{{ method }}</code>
      </label>
    </div>
    <p class="hint">{{ $t('adapters.bindingForm.webhookMethodsHint') }}</p>
  </div>

  <!-- Ingress allowlist (binding level) -->
  <div class="form-group">
    <label class="label">{{ $t('adapters.allowlist.bindingLabel') }}</label>
    <NetworkAllowlistEditor
      :model-value="cfg.allowed_networks ?? []"
      @update:model-value="cfg.allowed_networks = $event"
    />
  </div>

  <!-- Value source -->
  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.webhookValueSourceLabel') }}</label>
      <select v-model="cfg.value_source" class="input" data-testid="webhook-value-source">
        <option v-for="option in VALUE_SOURCES" :key="option.value" :value="option.value">{{ option.label }}</option>
      </select>
    </div>
    <div v-if="cfg.value_source === 'fixed'" class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.webhookFixedValueLabel') }}</label>
      <input v-model="cfg.fixed_value" class="input font-mono text-sm" data-testid="webhook-fixed-value" />
      <p class="hint">{{ $t('adapters.bindingForm.webhookFixedValueHint') }}</p>
    </div>
    <div v-else class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.webhookValueParamLabel') }}</label>
      <input v-model="cfg.value_param" class="input font-mono text-sm" data-testid="webhook-value-param" />
      <p class="hint">{{ $t('adapters.bindingForm.webhookValueParamHint', { name: cfg.value_param || 'value' }) }}</p>
    </div>
  </div>

  <!-- Auto-reset: turns the webhook into a trigger -->
  <div class="form-group">
    <div class="flex items-center gap-2">
      <input
        id="webhook-autoreset"
        type="checkbox"
        class="w-4 h-4 rounded"
        :checked="cfg.autoreset"
        data-testid="webhook-autoreset"
        @change="cfg.autoreset = $event.target.checked"
      />
      <label for="webhook-autoreset" class="text-sm text-slate-600 dark:text-slate-300">
        {{ $t('adapters.bindingForm.webhookAutoresetLabel') }}
      </label>
    </div>
    <p class="hint">{{ $t('adapters.bindingForm.webhookAutoresetHint') }}</p>
  </div>

  <div v-if="cfg.autoreset" class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.webhookAutoresetValueLabel') }}</label>
      <input v-model="cfg.autoreset_value" class="input font-mono text-sm" data-testid="webhook-autoreset-value" />
      <p class="hint">{{ $t('adapters.bindingForm.webhookAutoresetValueHint') }}</p>
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.webhookAutoresetDelayLabel') }}</label>
      <input
        v-model.number="cfg.autoreset_delay_ms"
        type="number"
        min="0"
        step="100"
        class="input"
        data-testid="webhook-autoreset-delay"
      />
      <p class="hint">{{ $t('adapters.bindingForm.webhookAutoresetDelayHint') }}</p>
    </div>
  </div>

  <!-- Debounce -->
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.webhookDebounceLabel') }}</label>
    <input v-model.number="cfg.debounce_ms" type="number" min="0" step="100" class="input" data-testid="webhook-debounce" />
    <p class="hint">{{ $t('adapters.bindingForm.webhookDebounceHint') }}</p>
  </div>

  <!-- Call URL — only available once the binding exists and has a token -->
  <template v-if="isExisting">
    <div class="optional-divider">{{ $t('adapters.bindingForm.webhookCallUrlSection') }}</div>

    <div v-if="loading" class="flex justify-center py-3"><Spinner size="sm" /></div>
    <!-- An error sits beside the entry, not instead of it: a failed rotation
         must leave the URL and the rotate button available for a retry. -->
    <p v-if="error" class="text-xs text-red-400" data-testid="webhook-error">{{ error }}</p>
    <template v-if="entry">
      <div class="form-group">
        <label class="label">{{ $t('adapters.bindingForm.webhookCallUrlLabel') }}</label>
        <div class="flex gap-2">
          <input :value="callUrl" class="input flex-1 font-mono text-xs" readonly data-testid="webhook-call-url" />
          <button type="button" class="btn-secondary px-3 text-sm whitespace-nowrap" @click="copy(callUrl)">
            {{ copied === callUrl ? $t('common.copied') : $t('common.copy') }}
          </button>
        </div>
        <p class="hint">{{ $t('adapters.bindingForm.webhookCallUrlHint') }}</p>
        <p
          v-if="originBlocked"
          class="mt-2 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-xs text-amber-600 dark:text-amber-400"
          data-testid="webhook-origin-warning"
        >
          {{ $t('adapters.allowlist.originBlocked', { host: originHost }) }}
        </p>
      </div>

      <div class="form-group">
        <label class="label">{{ $t('adapters.bindingForm.webhookCallUrlPathLabel') }}</label>
        <div class="flex gap-2">
          <input :value="callUrlInPath" class="input flex-1 font-mono text-xs" readonly data-testid="webhook-call-url-path" />
          <button type="button" class="btn-secondary px-3 text-sm whitespace-nowrap" @click="copy(callUrlInPath)">
            {{ copied === callUrlInPath ? $t('common.copied') : $t('common.copy') }}
          </button>
        </div>
        <p class="hint">{{ $t('adapters.bindingForm.webhookCallUrlPathHint') }}</p>
      </div>

      <div class="grid grid-cols-3 gap-4 text-xs text-slate-500">
        <div>
          <div class="text-slate-400">{{ $t('adapters.bindingForm.webhookCallCount') }}</div>
          <div class="font-mono" data-testid="webhook-call-count">{{ entry.call_count }}</div>
        </div>
        <div>
          <div class="text-slate-400">{{ $t('adapters.bindingForm.webhookPublishCount') }}</div>
          <div class="font-mono" data-testid="webhook-publish-count">{{ entry.publish_count }}</div>
        </div>
        <div>
          <div class="text-slate-400">{{ $t('adapters.bindingForm.webhookLastCalled') }}</div>
          <div class="font-mono" data-testid="webhook-last-called">{{ entry.last_called ?? $t('adapters.bindingForm.webhookNeverCalled') }}</div>
        </div>
      </div>

      <!-- Why calls were turned away — a 404 is indistinguishable by design -->
      <div
        v-if="rejectionSummary"
        class="p-2 rounded-lg bg-slate-100/80 dark:bg-slate-800/40 text-xs text-slate-600 dark:text-slate-300"
        data-testid="webhook-rejections"
      >
        <div>{{ $t('adapters.allowlist.rejectedCalls', { n: rejectionSummary.total }) }}</div>
        <div v-if="rejectionSummary.lastReason" class="mt-0.5 text-slate-500">
          {{ $t('adapters.allowlist.lastRejection', {
            reason: rejectionSummary.lastReason,
            host: rejectionSummary.lastClientIp || '—',
          }) }}
        </div>
      </div>

      <div
        v-if="instanceRejectionSummary"
        class="p-2 rounded-lg bg-slate-100/80 dark:bg-slate-800/40 text-xs text-slate-600 dark:text-slate-300"
        data-testid="webhook-instance-rejections"
      >
        <div>{{ $t('adapters.allowlist.instanceRejectedCalls', { n: instanceRejectionSummary.total }) }}</div>
        <div v-if="instanceRejectionSummary.lastReason" class="mt-0.5 text-slate-500">
          {{ $t('adapters.allowlist.lastRejection', {
            reason: instanceRejectionSummary.lastReason,
            host: instanceRejectionSummary.lastClientIp || '—',
          }) }}
        </div>
      </div>

      <div class="flex items-center gap-3">
        <button type="button" class="btn-secondary btn-sm" :disabled="rotating" @click="$emit('rotate-token')" data-testid="webhook-rotate-token">
          <Spinner v-if="rotating" size="sm" />
          {{ $t('adapters.bindingForm.webhookRotateToken') }}
        </button>
        <p class="hint">{{ $t('adapters.bindingForm.webhookRotateTokenHint') }}</p>
      </div>
    </template>
  </template>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import HelpButton from '@/components/ui/HelpButton.vue'
import NetworkAllowlistEditor from '@/components/ui/NetworkAllowlistEditor.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { copyText } from '@/utils/clipboard'
import { isAddressCovered, isLoopbackHost } from '@/utils/ipAllowlist'

const { t } = useI18n()

const props = defineProps({
  cfg: { type: Object, required: true },
  isExisting: { type: Boolean, default: false },
  entry: { type: [Object, null], default: null },
  loading: { type: Boolean, default: false },
  error: { type: [String, null], default: null },
  rotating: { type: Boolean, default: false },
  // Rejections the server could not attribute to a binding (unknown slug, wrong
  // token before the slug matched) — only the instance-level counters see them.
  instanceRejections: { type: [Object, null], default: null },
})

defineEmits(['rotate-token'])

const METHODS = ['GET', 'POST']

// A config loaded from an older binding (or a half-filled new one) can arrive
// without the list, and the template must not crash on it.
const selectedMethods = computed(() => (Array.isArray(props.cfg.methods) ? props.cfg.methods : []))

const VALUE_SOURCES = computed(() => [
  { value: 'fixed', label: t('adapters.bindingForm.webhookValueSourceFixed') },
  { value: 'request', label: t('adapters.bindingForm.webhookValueSourceRequest') },
])

// Both are read only from inside `v-else-if="entry"`, and a computed is lazy,
// so neither needs its own guard against a missing entry.
const callUrl = computed(() => `${window.location.origin}${props.entry.call_path}`)
const callUrlInPath = computed(() => `${window.location.origin}${props.entry.call_path_token_in_path}`)

const copied = ref(null)

const originHost = window.location.hostname

/**
 * Whether this binding's allowlist would reject a call from a browser on the
 * OBS host itself.
 *
 * Only a loopback hostname gives a verdict. Otherwise the hostname is the
 * *destination* address, not the caller's: a browser on 192.168.1.20 opening
 * OBS at 192.168.1.10 is seen by the server as 192.168.1.20, and a browser
 * cannot learn its own outbound address. Judging those against the allowlist
 * would warn about working URLs or stay silent about rejected ones, so they get
 * no verdict at all. `isAddressCovered` also returns null when it cannot decide
 * (an IPv6 entry), and a wrong warning would be worse than none.
 */
const originBlocked = computed(
  () => isLoopbackHost(originHost) && isAddressCovered(originHost, props.cfg.allowed_networks ?? []) === false,
)

function summarizeRejections(rejections) {
  if (!rejections || !rejections.total) return null
  return {
    total: rejections.total,
    lastReason: rejections.last_reason ? t(`adapters.allowlist.reason.${rejections.last_reason}`) : '',
    lastClientIp: rejections.last_client_ip,
  }
}

const rejectionSummary = computed(() => summarizeRejections(props.entry?.rejections))
const instanceRejectionSummary = computed(() => summarizeRejections(props.instanceRejections))

function toggleMethod(method) {
  const current = [...selectedMethods.value]
  const index = current.indexOf(method)
  if (index === -1) current.push(method)
  else current.splice(index, 1)
  // Never leave the binding unreachable: the last remaining method stays on.
  props.cfg.methods = current.length > 0 ? METHODS.filter(m => current.includes(m)) : [method]
}

async function copy(text) {
  try {
    await copyText(text)
    copied.value = text
  } catch {
    copied.value = null
  }
}
</script>
