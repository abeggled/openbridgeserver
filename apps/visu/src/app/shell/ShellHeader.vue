<script setup lang="ts">
/**
 * app/shell/ShellHeader — the default `header` slot fill (A3, Issue #99).
 *
 * Port of the prototype's RoomBar (`reference/vue-ionic/screens.js`): a menu
 * affordance, the section title, and — when no titlebar is shown — the clock /
 * messages pill on the trailing edge (store.js: the pill lives in the appbar when
 * `showTitlebar`, else in the room bar). A skin may replace this whole slot; this
 * is the host default so the shell is never empty.
 */
import { IonButtons, IonMenuButton } from '@ionic/vue';
import ClockPill from './ClockPill.vue';

defineProps<{
  /** The active section title (already localised by the caller). */
  title: string;
  /** Show the inline clock pill (true when the brand titlebar is hidden). */
  withClock?: boolean;
  /** Unread state forwarded to the pill. */
  unread?: boolean;
}>();

const emit = defineEmits<{ (e: 'read'): void }>();
</script>

<template>
  <div class="shell-header" :class="{ 'has-clock': withClock }">
    <IonButtons>
      <IonMenuButton />
    </IonButtons>
    <span class="shell-header-title">{{ title }}</span>
    <ClockPill v-if="withClock" class="shell-header-clock" :unread="unread" @read="emit('read')" />
  </div>
</template>

<style scoped>
.shell-header {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 9px 12px;
  min-height: 44px; /* AA touch target floor */
}

.shell-header-title {
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.shell-header-clock {
  margin-left: auto;
}
</style>
