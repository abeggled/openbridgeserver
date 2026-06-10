<script setup lang="ts">
/**
 * app/AppShell — the Ionic application shell (A3, Issue #99).
 *
 * The host's chrome around the rendered page: an `ion-app` root, a side
 * navigation menu (the top-level sections from `useShellState`, in source
 * order), an optional brand titlebar, the section header (RoomBar), and the
 * scrollable content where the page's tiles live. It owns the shell UI state
 * (active nav · unread · showTitlebar) — the skin owns no state (Goldene Regel
 * 1/4); it only fills the shell's slots.
 *
 * Shell-Slots v1 — each has a host default, and a skin/page may override it:
 *   - `background`  decorative backdrop layer behind the content
 *   - `header`      the top bar (default: menu + title + clock pill)
 *   - `roomDivider` the per-group label (default: accent dot + room name + count)
 *   - `empty`       shown when a section has nothing to render
 *   - `error`       shown for a hard failure (unknown skin / render gap)
 *   - default slot  the page body (tiles); the host lays the rooms out as the
 *                   floor (order + grouping), the page/skin renders the tiles.
 *
 * Must-keep (A3): the clock/messages pill pulses on `unread`; the titlebar is
 * optional (`showTitlebar`); the overview is room-grouped — the gap between
 * groups reads as "another room" (Goldene Regel 5). Safe-area insets are wired
 * via CSS `env(safe-area-inset-*)` so notches/home-indicators are respected.
 *
 * Slot props let a skin reuse the host's data without owning it:
 *   - `header`      { title, withClock, unread }
 *   - `roomDivider` { room, count } — exposed per group via the default slot's
 *                   own iteration; here the slot is offered shell-wide so a skin
 *                   that draws its own grouping can pull the divider component.
 */
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  IonApp,
  IonContent,
  IonHeader,
  IonMenu,
  IonList,
  IonItem,
  IonLabel,
  IonPage,
  IonRouterOutlet,
  IonToolbar,
  IonTitle,
  menuController,
} from '@ionic/vue';

import { useShellState, type NavKey, type ShellStateOptions } from './shell/useShellState';
import ShellHeader from './shell/ShellHeader.vue';
import ShellBackground from './shell/ShellBackground.vue';
import RoomDivider from './shell/RoomDivider.vue';
import ShellEmpty from './shell/ShellEmpty.vue';
import ShellError from './shell/ShellError.vue';
import type { RootTweakStyle } from '@obs-visu-skins/ionic';

const props = withDefaults(
  defineProps<{
    /** Seed the host shell state (active section · unread · showTitlebar). */
    state?: ShellStateOptions;
    /** A hard error to surface in the `error` slot (unknown skin / render gap). */
    error?: string | null;
    /** Whether the page body is empty (drives the `empty` slot fallback). */
    empty?: boolean;
    /** Render the embedded `ion-router-outlet` (the app) vs. only the default slot (tests/pages). */
    withRouterOutlet?: boolean;
    /** The page's skin root bindings (data-theme + tweak CSS vars). Applied to the
     *  page so the whole shell — header included — sits inside the themed surface:
     *  toolbars go transparent + themed (ionic.css .visu-root) and the photo/
     *  gradient background spans behind the chrome. */
    rootBind?: RootTweakStyle;
  }>(),
  { state: undefined, error: null, empty: false, withRouterOutlet: false, rootBind: undefined },
);

const { t } = useI18n();

const shell = useShellState(props.state);

/** Active section title, localised — fed to the header slot. */
const activeTitle = computed(() => t(`shell.nav.${shell.active.value}`));

/** Clock pill lives inline in the header only when no brand titlebar is shown. */
const headerWithClock = computed(() => !shell.showTitlebar.value);

function selectNav(key: NavKey): void {
  shell.setNav(key);
  void menuController.close();
}

defineExpose({ shell });
</script>

<template>
  <IonApp class="app-shell">
    <!-- Navigation: the top-level sections in source order (the floor). -->
    <IonMenu content-id="app-shell-content" type="overlay">
      <IonHeader>
        <IonToolbar>
          <IonTitle>{{ t('shell.nav.menuTitle') }}</IonTitle>
        </IonToolbar>
      </IonHeader>
      <IonContent>
        <IonList>
          <IonItem
            v-for="key in shell.nav"
            :key="key"
            button
            :detail="false"
            :class="{ active: key === shell.active.value }"
            @click="selectNav(key)"
          >
            <IonLabel>{{ t(`shell.nav.${key}`) }}</IonLabel>
          </IonItem>
        </IonList>
      </IonContent>
    </IonMenu>

    <IonPage
      id="app-shell-content"
      class="visu-root app-shell-page"
      v-bind="rootBind?.attrs"
      :style="rootBind?.style"
    >
      <!-- Optional brand titlebar (store.js → showTitlebar). Holds the clock pill
           when shown; otherwise the pill rides in the header below. -->
      <IonHeader v-if="shell.showTitlebar.value" class="app-shell-titlebar">
        <IonToolbar>
          <IonTitle>{{ t('shell.titlebar.brand') }}</IonTitle>
          <slot name="header" :title="activeTitle" :with-clock="true" :unread="shell.unread.value">
            <ShellHeader :title="activeTitle" :with-clock="true" :unread="shell.unread.value" @read="shell.markRead" />
          </slot>
        </IonToolbar>
      </IonHeader>

      <!-- Section header (RoomBar). Skin may replace via the `header` slot. -->
      <IonHeader v-else class="app-shell-header">
        <IonToolbar>
          <slot name="header" :title="activeTitle" :with-clock="headerWithClock" :unread="shell.unread.value">
            <ShellHeader
              :title="activeTitle"
              :with-clock="headerWithClock"
              :unread="shell.unread.value"
              @read="shell.markRead"
            />
          </slot>
        </IonToolbar>
      </IonHeader>

      <IonContent class="app-shell-content">
        <!-- Decorative backdrop layer (skin may override). -->
        <slot name="background">
          <ShellBackground />
        </slot>

        <div class="app-shell-body">
          <!-- Hard failure: surfaced loudly, never a silent gap. -->
          <slot v-if="error" name="error" :message="error">
            <ShellError :message="error" />
          </slot>

          <!-- Empty section. -->
          <slot v-else-if="empty" name="empty">
            <ShellEmpty />
          </slot>

          <!-- The page body. The default slot receives the RoomDivider component
               so a page/skin can draw the per-group label with the host default;
               the embedded router outlet is opt-in (the running app). -->
          <template v-else>
            <slot :room-divider="RoomDivider" :shell="shell" />
            <IonRouterOutlet v-if="withRouterOutlet" />
          </template>
        </div>
      </IonContent>
    </IonPage>
  </IonApp>
</template>

<style scoped>
/* Safe-area insets — respect notches / home indicators (prepared in M2). The
   shell pads its chrome by the device insets so nothing sits under a cutout. */
.app-shell-titlebar,
.app-shell-header {
  padding-top: env(safe-area-inset-top, 0px);
}

.app-shell-header {
  padding-left: env(safe-area-inset-left, 0px);
  padding-right: env(safe-area-inset-right, 0px);
}

.app-shell-content {
  --padding-start: env(safe-area-inset-left, 0px);
  --padding-end: env(safe-area-inset-right, 0px);
  --padding-bottom: env(safe-area-inset-bottom, 0px);
}

.app-shell-body {
  position: relative;
  z-index: 1; /* above the decorative background layer */
}

/* When the page is the skin's themed surface, the chrome is glass over the
   background: content transparent so the page photo/gradient shows through, and
   the toolbars get a frosted backdrop (their transparent fill + colour come from
   ionic.css .visu-root → --ion-toolbar-background / --ion-toolbar-color). */
.app-shell-page.visu-root .app-shell-content {
  --background: transparent;
}
.app-shell-page.visu-root .app-shell-header ion-toolbar,
.app-shell-page.visu-root .app-shell-titlebar ion-toolbar {
  backdrop-filter: blur(16px) saturate(1.3);
  -webkit-backdrop-filter: blur(16px) saturate(1.3);
}

/* Active nav entry — additive accent, legible in every theme. */
.app-shell-page :deep(ion-item.active) {
  --color: var(--obs-accent, var(--ion-color-primary));
  font-weight: 700;
}
</style>
