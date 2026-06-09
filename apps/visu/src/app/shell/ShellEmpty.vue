<script setup lang="ts">
/**
 * app/shell/ShellEmpty — the default `empty` slot fill (A3, Issue #99).
 *
 * Shown when a section has nothing to render. A skin may replace this slot; the
 * host default is a quiet, centred, localised message so the user is never left
 * staring at a blank page. Text goes through i18n (no hardcoded user-facing
 * strings).
 */
import { useI18n } from 'vue-i18n';
const { t } = useI18n();
</script>

<template>
  <div class="shell-empty" role="status">
    <p class="shell-empty-text">
      {{ t('shell.empty') }}
    </p>
  </div>
</template>

<style scoped>
.shell-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 40vh;
  padding: 24px;
  text-align: center;
}

.shell-empty-text {
  margin: 0;
  font-size: 15px;
  opacity: 0.7;
}
</style>
