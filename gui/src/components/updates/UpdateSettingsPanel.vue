<template>
  <div class="card" data-testid="update-settings">
    <div class="card-header">
      <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('updates.title') }}</h3>
      <HelpButton help-id="settings-updates" />
    </div>
    <div class="card-body flex flex-col gap-4">
      <div v-if="!status && store.statusError" class="flex items-center gap-3 text-xs text-red-500" data-testid="update-status-error">
        <span>{{ $t('updates.statusLoadError') }}</span>
        <button class="btn-secondary btn-sm" data-testid="update-status-retry" @click="store.fetchStatus()">{{ $t('updates.retry') }}</button>
      </div>
      <div v-else-if="!status" class="flex justify-center py-4"><Spinner /></div>
      <template v-else>
        <p v-if="!status.enabled" class="text-xs text-slate-500" data-testid="update-disabled">{{ $t('updates.status.disabled') }}</p>

        <div class="form-group">
          <label class="label" for="update-channel">{{ $t('updates.channel') }}</label>
          <select
            v-if="auth.isAdmin"
            id="update-channel"
            :value="status.channel"
            :disabled="store.saving"
            class="input text-sm"
            data-testid="update-channel-select"
            @change="onChannelChange($event.target)"
          >
            <option v-for="channel in status.channels" :key="channel" :value="channel">{{ $t(`updates.channels.${channel}`) }}</option>
          </select>
          <span v-else class="text-sm text-slate-700 dark:text-slate-200" data-testid="update-channel-text">{{ $t(`updates.channels.${status.channel}`) }}</span>
          <p class="text-xs text-slate-500 mt-1">{{ $t(`updates.channelHints.${status.channel}`) }}</p>
        </div>

        <dl class="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
          <dt class="text-slate-500">{{ $t('updates.status.installed') }}</dt>
          <dd class="font-mono text-slate-700 dark:text-slate-200" data-testid="update-installed">{{ status.installed_version }}</dd>
          <dt class="text-slate-500">{{ $t('updates.status.latest') }}</dt>
          <dd class="font-mono text-slate-700 dark:text-slate-200" data-testid="update-latest">{{ status.latest_version || $t('updates.status.unknown') }}</dd>
          <dt class="text-slate-500">{{ $t('updates.status.availability') }}</dt>
          <dd data-testid="update-availability">
            <Badge :variant="availability.variant" size="xs">{{ $t(availability.key) }}</Badge>
          </dd>
          <dt class="text-slate-500">{{ $t('updates.status.lastSuccess') }}</dt>
          <dd class="text-slate-700 dark:text-slate-200" data-testid="update-last-success">{{ fmtOptional(status.last_success_at) }}</dd>
          <dt class="text-slate-500">{{ $t('updates.status.lastCheck') }}</dt>
          <dd class="text-slate-700 dark:text-slate-200" data-testid="update-last-check">{{ fmtOptional(status.last_check_at) }}</dd>
          <template v-if="status.enabled">
            <dt class="text-slate-500">{{ $t('updates.status.nextCheck') }}</dt>
            <dd class="text-slate-700 dark:text-slate-200" data-testid="update-next-check">{{ fmtOptional(status.next_check_at) }}</dd>
          </template>
        </dl>

        <div v-if="status.last_error" class="text-xs text-red-500 dark:text-red-400 flex flex-col gap-0.5" data-testid="update-error">
          <span>{{ errorText }}</span>
          <span v-if="status.stale" class="text-amber-600 dark:text-amber-400" data-testid="update-stale">{{ $t('updates.status.stale') }}</span>
        </div>
        <p v-if="actionError" class="text-xs text-red-500" data-testid="update-action-error">{{ actionError }}</p>

        <div class="flex items-center gap-2 flex-wrap">
          <button
            v-if="auth.isAdmin && status.enabled"
            class="btn-primary btn-sm"
            :disabled="store.checking"
            data-testid="update-check-now"
            @click="onCheckNow"
          >
            <Spinner v-if="store.checking" size="sm" />
            {{ $t('updates.checkNow') }}
          </button>
          <button
            v-if="status.release_notes_count > 0"
            class="btn-secondary btn-sm"
            data-testid="update-open-notes"
            @click="notesOpen = true"
          >
            {{ $t('updates.notice.releaseNotes') }}
          </button>
        </div>
      </template>
    </div>
    <ReleaseNotesModal v-model="notesOpen" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import HelpButton from '@/components/ui/HelpButton.vue'
import Spinner from '@/components/ui/Spinner.vue'
import ReleaseNotesModal from '@/components/updates/ReleaseNotesModal.vue'
import { useTz } from '@/composables/useTz'
import { useAuthStore } from '@/stores/auth'
import { useUpdatesStore } from '@/stores/updates'

const { t, te } = useI18n()
const auth = useAuthStore()
const store = useUpdatesStore()
const { fmtDateTime } = useTz()
const status = computed(() => store.status)
const notesOpen = ref(false)
const actionError = ref('')

const availability = computed(() => {
  if (status.value.update_available === true) return { variant: 'warning', key: 'updates.status.available' }
  if (status.value.update_available === false) return { variant: 'success', key: 'updates.status.upToDate' }
  return { variant: 'muted', key: 'updates.status.unknown' }
})

const errorText = computed(() => {
  const key = `updates.errors.${status.value.last_error}`
  return te(key) ? t(key) : t('updates.errors.internal')
})

function fmtOptional(iso) {
  return iso ? fmtDateTime(iso) : t('updates.status.never')
}

async function onCheckNow() {
  actionError.value = ''
  try {
    await store.checkNow()
  } catch {
    actionError.value = t('updates.actionFailed')
  }
}

async function onChannelChange(select) {
  actionError.value = ''
  try {
    await store.setChannel(select.value)
  } catch {
    // :value is one-way and status.channel did not change, so Vue would not
    // re-render: put the select back on the channel the server still has.
    select.value = status.value.channel
    actionError.value = t('updates.actionFailed')
  }
}

onMounted(() => {
  store.fetchStatus()
})
</script>
