<template>
  <!-- pointer-events-auto: several Settings tabs wrap their whole body in
       pointer-events-none while in demo mode (read-only), and this button
       often sits inside that wrapper — help is informational, not an edit
       action, so it must stay clickable even there. -->
  <button
    type="button"
    :class="compact
      ? 'p-0.5 rounded hover:bg-slate-100 dark:hover:bg-slate-700/60 text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100 transition-colors pointer-events-auto'
      : 'btn-icon pointer-events-auto'"
    :aria-label="$t('help.openLabel')"
    :title="$t('help.openLabel')"
    :data-testid="`help-button-${helpId}`"
    @click="helpStore.open(props.helpId)"
  >
    <svg :class="compact ? 'w-3 h-3' : 'w-4 h-4'" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        stroke-linecap="round"
        stroke-linejoin="round"
        stroke-width="2"
        d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
      />
    </svg>
  </button>
</template>

<script setup>
import { useHelpStore } from '@/stores/help'

const props = defineProps({
  helpId: { type: String, required: true },
  // Smaller footprint (20px vs the default 32px) for dense lists like the
  // Logic Module's block palette, where the default btn-icon size nearly
  // doubles each row's height (issue feedback: ~45px vs ~26px for a row
  // with no button yet).
  compact: { type: Boolean, default: false },
})

const helpStore = useHelpStore()
</script>
