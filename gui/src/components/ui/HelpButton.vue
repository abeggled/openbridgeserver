<template>
  <!-- pointer-events-auto: several Settings tabs wrap their whole body in
       pointer-events-none while in demo mode (read-only), and this button
       often sits inside that wrapper — help is informational, not an edit
       action, so it must stay clickable even there. -->
  <button
    type="button"
    class="btn-icon pointer-events-auto"
    :aria-label="$t('help.openLabel')"
    :title="$t('help.openLabel')"
    :data-testid="`help-button-${helpId}`"
    @click="helpStore.open(props.helpId)"
  >
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
})

const helpStore = useHelpStore()
</script>
