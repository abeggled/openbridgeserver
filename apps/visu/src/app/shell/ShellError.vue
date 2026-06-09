<script setup lang="ts">
/**
 * app/shell/ShellError — the default `error` slot fill (A3, Issue #99).
 *
 * Surfaces a hard failure (e.g. an unknown skin key or a render gap — the host
 * never papers a gap over with a silent default; Goldene Regel 2/3). A skin may
 * replace this slot, but the host default makes the failure loud and legible: an
 * `alert` role + a localised heading + the raw message for diagnosis.
 */
import { useI18n } from 'vue-i18n';

defineProps<{
  /** The error message to surface (developer/diagnostic detail). */
  message?: string;
}>();

const { t } = useI18n();
</script>

<template>
  <div class="shell-error" role="alert">
    <p class="shell-error-title">
      {{ t('shell.error.title') }}
    </p>
    <p v-if="message" class="shell-error-detail">
      {{ message }}
    </p>
  </div>
</template>

<style scoped>
.shell-error {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
  min-height: 40vh;
  padding: 24px;
  text-align: center;
}

.shell-error-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
}

.shell-error-detail {
  margin: 0;
  font-size: 13px;
  opacity: 0.75;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-word;
}
</style>
