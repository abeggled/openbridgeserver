<template>
  <div class="missing-widget">
    <span class="missing-widget__badge" :aria-label="$t('widgets.missing.ariaLabel')">!</span>
    <div class="missing-widget__text">
      <div class="missing-widget__title">{{ $t('widgets.missing.title') }}</div>
      <div class="missing-widget__type">{{ widgetType }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

useI18n()

defineProps<{ widgetType: string }>()
</script>

<style scoped>
.missing-widget {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  height: 100%;
  border: 2px dashed #ef4444;
  border-radius: 6px;
  background: rgba(239, 68, 68, 0.07);
  padding: 4px 8px;
  box-sizing: border-box;
}
.missing-widget__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #ef4444;
  color: #fff;
  font-weight: 700;
  font-size: 0.8rem;
  flex-shrink: 0;
}
.missing-widget__text { min-width: 0; }
.missing-widget__title {
  font-size: 0.6rem;
  font-weight: 600;
  color: #ef4444;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.missing-widget__type {
  font-size: 0.65rem;
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
