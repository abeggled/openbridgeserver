<template>
  <div
    v-if="store.updateAvailable"
    class="mx-6 mt-4 card border-l-4 border-amber-500 px-4 py-3 flex items-start gap-3 flex-wrap"
    role="status"
    data-testid="update-notice"
  >
    <svg class="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
    </svg>
    <div class="flex-1 min-w-0 flex flex-col gap-0.5">
      <span class="text-sm font-semibold text-slate-800 dark:text-slate-100">
        {{ $t('updates.notice.title', { version: status.latest_version }) }}
      </span>
      <span class="text-xs text-slate-500 dark:text-slate-400" data-testid="update-notice-details">
        {{ $t('updates.notice.details', {
          channel: $t(`updates.channels.${status.channel}`),
          installed: status.installed_version,
          latest: status.latest_version,
          checked: fmtDateTime(status.last_success_at),
        }) }}
      </span>
      <span v-if="status.stale" class="text-xs text-amber-600 dark:text-amber-400" data-testid="update-notice-stale">
        {{ $t('updates.status.stale') }}
      </span>
    </div>
    <div class="flex items-center gap-2 shrink-0">
      <button class="btn-secondary btn-sm" data-testid="update-notice-notes" @click="notesOpen = true">
        {{ $t('updates.notice.releaseNotes') }}
      </button>
      <RouterLink to="/settings?tab=updates" class="text-xs text-blue-400 hover:underline" data-testid="update-notice-settings" @click="openUpdatesTab">
        {{ $t('updates.notice.openSettings') }}
      </RouterLink>
      <HelpButton help-id="settings-updates" />
    </div>
  </div>
  <!-- Outside the notice: a poll, check or channel switch that clears
       update_available must not close the notes the user is reading. -->
  <ReleaseNotesModal v-model="notesOpen" />
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import HelpButton from '@/components/ui/HelpButton.vue'
import ReleaseNotesModal from '@/components/updates/ReleaseNotesModal.vue'
import { useTz } from '@/composables/useTz'
import { useAuthStore } from '@/stores/auth'
import { useUpdatesStore } from '@/stores/updates'

// The backend re-checks every twelve hours; polling its cached status this
// often is enough to show a new result without a page reload.
const POLL_INTERVAL_MS = 15 * 60 * 1000

const auth = useAuthStore()
const store = useUpdatesStore()
const { fmtDateTime } = useTz()
const status = computed(() => store.status)
const notesOpen = ref(false)
let poll = null

onMounted(() => {
  // App.vue renders this layout briefly before the route (and its public flag)
  // is resolved: a request without a login would answer 401, whose interceptor
  // redirects to /login — and back here, in a loop. Same guard as Sidebar.vue.
  if (!auth.isLoggedIn) return
  store.fetchStatus()
  poll = window.setInterval(() => store.fetchStatus(), POLL_INTERVAL_MS)
})

// A modified click opens the link in a new tab or window, which Vue Router
// leaves to the browser: this window must then stay on its current tab.
function openUpdatesTab(event) {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  store.requestUpdatesTab()
}

onBeforeUnmount(() => {
  window.clearInterval(poll)
})
</script>
