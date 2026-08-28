<template>
  <Teleport to="body">
    <Transition
      enter-from-class="opacity-0 translate-x-full" enter-active-class="transition-all duration-200"
      leave-to-class="opacity-0 translate-x-full"   leave-active-class="transition-all duration-150"
    >
      <div
        v-if="helpStore.isOpen"
        class="fixed right-0 top-0 bottom-0 z-50 card shadow-2xl flex flex-col rounded-none border-l"
        :style="{ width: width + 'px', maxWidth: '90vw' }"
        data-testid="help-drawer-panel"
      >
        <div
          class="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-sky-400/40"
          @pointerdown="startResize"
          data-testid="help-drawer-resize-handle"
        />

        <!--
          Not .card-header: that class sizes itself from padding + content
          (py-4 around whichever child is tallest), which the drawer's own
          32px-tall icon buttons stretch to 65px — a few px taller than the
          Admin-GUI's own TopBar (issue feedback). TopBar avoids that same
          text-vs-button height mismatch with a fixed h-14 + items-center
          instead of vertical padding, so this mirrors that composition
          exactly to land on the same 56px header height.
        -->
        <div class="h-14 shrink-0 px-5 border-b border-slate-200 dark:border-slate-700/60 flex items-center justify-between">
          <h3 class="text-base font-semibold text-slate-800 dark:text-slate-100">
            {{ $t('help.title') }}
          </h3>
          <div class="flex items-center gap-1">
            <button
              v-if="iframeSrc"
              class="btn-icon"
              :aria-label="$t('help.openInNewTab')"
              :title="$t('help.openInNewTab')"
              data-testid="help-drawer-open-new-tab"
              @click="openInNewTab"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"/>
              </svg>
            </button>
            <button
              class="btn-icon"
              :aria-label="$t('common.close')"
              data-testid="help-drawer-close"
              @click="close"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
              </svg>
            </button>
          </div>
        </div>

        <div class="flex-1 min-h-0">
          <iframe
            v-if="iframeSrc"
            :src="iframeSrc"
            class="w-full h-full border-0"
            :title="$t('help.title')"
            data-testid="help-drawer-iframe"
          />
          <div v-else class="card-body text-sm text-slate-500 dark:text-slate-400">
            {{ helpStore.loadError ? $t('help.systemUnavailable') : $t('help.unavailable') }}
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
// Deliberately non-blocking (issue feedback after the first drawer landed):
// no full-viewport overlay/backdrop, so the rest of the Admin-GUI stays
// fully interactive while the drawer is open — the whole point of picking
// a slide-in drawer over a modal was to keep working context usable.
// Close only via ESC or the X button; there is no "click outside" area
// left to catch a close click, same trade-off as Modal.vue's softBackdrop.
import { computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useHelpStore } from '@/stores/help'
import { useSettingsStore } from '@/stores/settings'
import { useResizablePanel } from '@/composables/useResizablePanel'

const helpStore = useHelpStore()
const settings = useSettingsStore()
const { width, startResize } = useResizablePanel({
  storageKey: 'obs-help-drawer-width',
  defaultWidth: Math.round(window.innerWidth * 0.4),
  min: 320,
  max: 960,
})

// The main layout (App.vue) reserves this much space on the right instead of
// letting the drawer float on top of page content (issue feedback: form
// fields near the right edge were disappearing behind the drawer).
watch(width, (w) => helpStore.setDrawerWidth(w), { immediate: true })

// The help site is a separate document (own <html>, own localStorage key)
// even though it's same-origin — it doesn't know the Admin-GUI's current
// dark/light state on its own and would otherwise fall back to the
// browser's prefers-color-scheme, which can disagree (issue feedback: the
// help site's dark palette looked "slightly different" — turned out the
// iframe wasn't even reliably in dark mode to begin with). Passing it via
// query param lets help/.vitepress/config.mts seed VitePress's own
// localStorage key before VitePress's anti-FOUC script runs.
function withAppearance(url, isDark) {
  if (!url) return url
  const [path, hash] = url.split('#')
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}appearance=${isDark ? 'dark' : 'light'}${hash ? '#' + hash : ''}`
}

// settings.isDarkResolved is a reactive mirror of the <html class="dark">
// state applyTheme() sets — reading it (instead of the DOM class directly)
// makes this computed correctly re-run both when the theme is switched
// explicitly and when it's 'system' and the OS-level color scheme changes
// while the drawer is already open (App.vue's prefers-color-scheme
// listener calls applyTheme() too, without ever touching settings.theme).
const iframeSrc = computed(() => withAppearance(helpStore.currentUrl, settings.isDarkResolved))

function close() {
  helpStore.close()
}

function openInNewTab() {
  // Also closes the drawer: this button exists specifically for narrow
  // monitors where the reserved side-panel space is precious, so once the
  // content lives in its own tab there is no reason to keep it narrowed.
  window.open(iframeSrc.value, '_blank', 'noopener,noreferrer')
  close()
}

function onKeyDown(event) {
  if (event.key === 'Escape' && helpStore.isOpen) close()
}

onMounted(() => document.addEventListener('keydown', onKeyDown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeyDown))
</script>
