<script setup lang="ts">
/**
 * pages/OverviewPage — the room-grouped mobile overview (M2, the first Ionic page).
 *
 * This is the sichtbare M2-Deliverable: the prototype's `mobileGroups` overview,
 * now assembled from the *core* model and rendered through the *host* + the
 * `ionic` skin. The page is the author's choice of skin (`skin: 'ionic'`); it
 * owns no device state and no renderer — it only wires the host pieces together:
 *
 *   - AppShell (A3) provides the chrome: nav menu, header with the clock pill,
 *     the room-grouped body. The page fills the shell's default slot.
 *   - DetailModalHost (A2) provides the host API (gesture → canonical store
 *     action) and owns the detail surface. {@link OverviewGrid} (its descendant)
 *     captures a tap → canonical action and a long-press → `openDetail`.
 *   - SkinHost (A1/A4), inside the grid, turns the ordered, grouped `core/model`
 *     rooms into tiles via the ionic skin, addressed by type. Order + grouping
 *     are the floor; the span/row → role mapping (model.layoutRole) is additive.
 *   - TweaksPanel (A6) edits the ionic skin's manifest-declared tweaks; the page
 *     owns those values (skin owns no state) and feeds them to the skin root via
 *     `applyTweaks`.
 *
 * Goldene Regeln honoured: the skin owns no state (the page + store do); gestures
 * are mapped by the host, never the skin; the renderer is addressed by type (a
 * gap throws loudly); order + grouping are the floor; AA tokens come from core.
 */
import { ref, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { applyTweaks, type IonicTweaks } from '@obs-visu-skins/ionic';
import '@obs-visu-skins/ionic/ionic.css';

import AppShell from '../app/AppShell.vue';
import DetailModalHost from '../app/DetailModalHost.vue';
import TweaksPanel, { type TweakValues } from '../app/TweaksPanel.vue';
import OverviewGrid from './OverviewGrid';
import { rooms as modelRooms } from '../core/model';

/** The skin this page is authored against (no runtime skin switch). */
const SKIN = 'ionic';

const { t } = useI18n();

/** The ordered, room-grouped overview blocks (core/model → mobileGroups). */
const groups = computed(() => modelRooms);

/* ----------------------------------------------------------- tweak state (A6) */
// The page owns the per-page tweak values (the skin owns no state, golden rule 4).
// Seeded empty → TweaksPanel merges the skin's manifest defaults as the floor.
const tweaks = ref<TweakValues>({});
const showTweaks = ref(false);

/** Map the page's tweak values to the ionic skin root attrs + CSS vars (data → code). */
const rootTweaks = computed(() => applyTweaks(tweaks.value as IonicTweaks));

/** Active theme drives the AA-safe tokens the host hands each renderer (golden rule 6). */
const theme = computed<'light' | 'dark' | 'image'>(() => {
  const v = tweaks.value['theme'];
  return v === 'dark' || v === 'image' ? v : 'light';
});
</script>

<template>
  <AppShell class="overview-page" :state="{ active: 'overview' }" :root-bind="rootTweaks">
    <template #default>
      <DetailModalHost :skin="SKIN" :theme="theme">
        <div class="visu-root overview-root" v-bind="rootTweaks.attrs" :style="rootTweaks.style">
          <OverviewGrid :skin="SKIN" :groups="groups" :theme="theme" />
        </div>

        <!-- Tweaks editor (A6): the page owns the values; the skin reads them. -->
        <button
          type="button"
          class="overview-tweaks-toggle"
          :aria-expanded="showTweaks"
          @click="showTweaks = !showTweaks"
        >
          {{ t('overview.tweaks.toggle') }}
        </button>
        <TweaksPanel v-if="showTweaks" v-model="tweaks" :skin="SKIN" />
      </DetailModalHost>
    </template>
  </AppShell>
</template>

<style scoped>
.overview-root {
  /* Room blocks read as separate rooms by the gap between groups (Must-Keep);
     the ionic skin draws the gap via --vz-room-gap on the .visu-root. */
  display: block;
}

.overview-tweaks-toggle {
  margin: var(--obs-space, 12px);
  padding: 8px 14px;
  border-radius: 999px;
  border: 1px solid var(--ion-color-step-200, #cfd4dc);
  background: var(--ion-background-color, #fff);
  color: var(--ion-text-color, #1b2027);
  font: inherit;
  cursor: pointer;
}
</style>
