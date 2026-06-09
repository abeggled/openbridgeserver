<script setup lang="ts">
/**
 * app/shell/RoomDivider — the default `roomDivider` slot fill (A3, Issue #99).
 *
 * Port of the prototype room head (`reference/vue-ionic/screens.js`/css
 * `.vz-room-head`). On the room-grouped overview the *spacing* between blocks is
 * the floor signal ("you are now in another room", Goldene Regel 5); this divider
 * is the additive, ignorable label a skin may show above each group. The quiet
 * accent dot + uppercased room name + optional count mirror the prototype.
 *
 * A skin may replace this slot wholesale; the host default keeps the grouping
 * legible even with no skin chrome.
 */
defineProps<{
  /** The room/group name. */
  room: string;
  /** Optional entry count shown as quiet meta. */
  count?: number;
}>();
</script>

<template>
  <div class="room-divider">
    <span class="room-divider-name">{{ room }}</span>
    <span v-if="count != null" class="room-divider-meta">{{ count }}</span>
  </div>
</template>

<style scoped>
.room-divider {
  display: flex;
  align-items: baseline;
  gap: 9px;
  padding: 0 13px 9px;
}

.room-divider::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 3px;
  align-self: center;
  background: var(--obs-accent, currentColor);
}

.room-divider-name {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}

.room-divider-meta {
  margin-left: auto;
  font-size: 11px;
  font-weight: 600;
  opacity: 0.7;
  font-variant-numeric: tabular-nums;
}
</style>
