<script setup lang="ts">
/**
 * app/shell/ShellBackground — the default `background` slot fill (A3, Issue #99).
 *
 * The prototype's "glass" theme paints a photo behind the tiles
 * (`reference/vue-ionic/app.js → --vz-photo`). A skin may fill this slot with its
 * own backdrop (image/gradient); the host default is a plain, decorative,
 * non-interactive layer so the shell composites correctly even with no skin
 * backdrop. It is `aria-hidden` — purely decorative.
 */
</script>

<template>
  <div class="shell-background" aria-hidden="true" />
</template>

<style scoped>
.shell-background {
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background: var(--obs-shell-bg, transparent);
}
</style>
