<script setup lang="ts">
/**
 * app/shell/ClockPill — the date/time pill that doubles as the messages button
 * (A3 must-keep; port of reference/vue-ionic/app.js → VzClock).
 *
 * Shows the current date + time, ticking once a minute. While `unread` is true it
 * pulses in the accent colour and exposes a dot; tapping it emits `read` so the
 * host clears the unread flag (the skin owns no state — the host owns `unread`,
 * the pill only emits the intent). The pulse honours `prefers-reduced-motion`.
 *
 * Locale-aware: date/time use the active i18n locale via `Intl`. The aria-label
 * switches between "unread messages" / "messages" so screen readers announce the
 * state (AA-Pflicht / Goldene Regel 6).
 */
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';

const props = defineProps<{
  /** Whether there are unread messages — drives the pulse + dot. */
  unread?: boolean;
}>();

const emit = defineEmits<{
  /** The user tapped the pill — the host should mark messages read. */
  (e: 'read'): void;
}>();

const { t, locale } = useI18n();

const now = ref<Date>(new Date());
let timer: ReturnType<typeof setInterval> | null = null;

// store.js ticks the clock on an interval; once a minute is enough for HH:MM.
onMounted(() => {
  timer = setInterval(() => (now.value = new Date()), 20_000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});

const date = computed(() =>
  now.value.toLocaleDateString(locale.value, { day: '2-digit', month: '2-digit', year: 'numeric' }),
);
const time = computed(() => now.value.toLocaleTimeString(locale.value, { hour: '2-digit', minute: '2-digit' }));

const label = computed(() => (props.unread ? t('shell.clock.unread') : t('shell.clock.messages')));

function onClick(): void {
  emit('read');
}
</script>

<template>
  <button type="button" class="clock-pill" :class="{ unread: props.unread }" :aria-label="label" @click="onClick">
    <span class="clock-txt">
      <span class="d">{{ date }}</span>
      <span class="t">{{ time }}</span>
    </span>
    <span v-if="props.unread" class="clock-dot" aria-hidden="true" />
  </button>
</template>

<style scoped>
/* Port of reference visu-ionic.css .vz-clock-btn / .vz-clock-dot / pulse. */
.clock-pill {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border: 1px solid color-mix(in oklab, var(--obs-accent, currentColor) 30%, transparent);
  border-radius: 999px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  line-height: 1.12;
  font: inherit;
}

.clock-txt {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.clock-txt .d {
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  opacity: 0.75;
}

.clock-txt .t {
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  color: var(--obs-accent, currentColor);
}

.clock-dot {
  position: absolute;
  top: -3px;
  right: -3px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--obs-accent, currentColor);
  box-shadow: 0 0 0 2px color-mix(in oklab, var(--obs-accent, currentColor) 28%, transparent);
}

.clock-pill.unread {
  animation: clock-pulse 1.8s ease-in-out infinite;
}

@keyframes clock-pulse {
  0%,
  100% {
    border-color: color-mix(in oklab, var(--obs-accent, currentColor) 55%, transparent);
    box-shadow: 0 0 0 0 color-mix(in oklab, var(--obs-accent, currentColor) 55%, transparent);
  }
  50% {
    border-color: var(--obs-accent, currentColor);
    box-shadow: 0 0 0 7px color-mix(in oklab, var(--obs-accent, currentColor) 0%, transparent);
  }
}

@media (prefers-reduced-motion: reduce) {
  .clock-pill.unread {
    animation: none;
    border-color: var(--obs-accent, currentColor);
  }
}
</style>
