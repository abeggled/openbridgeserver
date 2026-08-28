import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { helpApi } from '@/api/client'
import i18n from '@/i18n'

/**
 * Drives the integrated help drawer (#896). A `help_id` (a heading anchor id
 * assigned in the help/ VitePress content, see help/scripts/generate-help-index.mjs)
 * resolves via help-index.json to a locale-specific /help/... URL, embedded in
 * the drawer as an iframe.
 */
export const useHelpStore = defineStore('help', () => {
  const isOpen = ref(false)
  const currentHelpId = ref(null)
  const helpIndex = ref(null)
  const loadError = ref(false)
  // Mirrors HelpDrawer's own resizable width (useResizablePanel, component-owned
  // for correct mount/unmount lifecycle) so the main layout can reserve exactly
  // that much space instead of the drawer floating on top of page content.
  const drawerWidth = ref(0)
  let loadPromise = null

  function setDrawerWidth(w) {
    drawerWidth.value = w
  }

  // The narrowest a full-viewport overlay (Modal.vue's dialog, or App.vue's
  // main content) may be squeezed to by the reservation below — without this
  // floor, a modal on a narrow/phone-sized viewport could be reserved down to
  // a few unusable pixels beside a wide drawer (Codex review on PR #1180).
  const MIN_REMAINING_WIDTH_PX = 300

  // Shared with App.vue (main-content margin) and every full-viewport overlay
  // (Modal.vue, HierarchyManager.vue's bespoke dialogs): the space the drawer
  // occupies on the right, so popups center over the remaining OBS area
  // instead of the drawer becoming unreadable under a dialog's backdrop
  // (issue feedback). `min(...)` mirrors HelpDrawer.vue's own `maxWidth: '90vw'`
  // rather than clamping drawerWidth in JS, so it stays correct across later
  // viewport resizes with no resize-event listener needed — the third term
  // caps the reservation so at least MIN_REMAINING_WIDTH_PX always remains
  // (falling back to 0, i.e. the overlay covers the drawer, once the viewport
  // itself is narrower than that).
  const reservedRight = computed(() => (
    isOpen.value
      ? `min(${drawerWidth.value}px, 90vw, max(0px, 100vw - ${MIN_REMAINING_WIDTH_PX}px))`
      : '0px'
  ))

  function loadIndex() {
    if (helpIndex.value || loadPromise) return loadPromise
    loadPromise = helpApi.index()
      .then(({ data }) => {
        helpIndex.value = data
        loadError.value = false
      })
      .catch(() => {
        loadError.value = true
        loadPromise = null // allow a retry on the next open()
      })
    return loadPromise
  }

  // GUI supports more locales (de/en/es/fr/it/gsw) than the help site does
  // today (de/en) — fall back to de (the Weblate source language, always
  // authored first) so a help_id still resolves to *something* useful.
  const currentUrl = computed(() => {
    const entry = currentHelpId.value ? helpIndex.value?.helpIds?.[currentHelpId.value] : null
    if (!entry) return null
    const locale = i18n.global.locale.value
    return entry[locale] ?? entry.de ?? Object.values(entry)[0] ?? null
  })

  function open(helpId) {
    currentHelpId.value = helpId
    isOpen.value = true
    loadIndex()
  }

  function close() {
    isOpen.value = false
  }

  return {
    isOpen,
    currentHelpId,
    helpIndex,
    loadError,
    drawerWidth,
    currentUrl,
    reservedRight,
    loadIndex,
    open,
    close,
    setDrawerWidth,
  }
})
