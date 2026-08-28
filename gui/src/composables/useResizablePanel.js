import { ref, onBeforeUnmount } from 'vue'

/**
 * Drag-to-resize for a side panel anchored to the right edge of its container.
 * The handle sits on the panel's left edge; dragging it left widens the panel.
 * Width persists per `storageKey` in localStorage across sessions.
 */
export function useResizablePanel({ storageKey, defaultWidth = 288, min = 240, max = 640 } = {}) {
  const stored = storageKey ? Number(localStorage.getItem(storageKey)) : NaN
  const clamp = (w) => Math.min(max, Math.max(min, w))
  const width = ref(clamp(Number.isFinite(stored) && stored > 0 ? stored : defaultWidth))
  const isResizing = ref(false)

  let startX = 0
  let startWidth = 0

  function onPointerMove(e) {
    width.value = clamp(startWidth + (startX - e.clientX))
  }

  function stopResize() {
    if (!isResizing.value) return
    isResizing.value = false
    document.removeEventListener('pointermove', onPointerMove)
    document.removeEventListener('pointerup', stopResize)
    document.removeEventListener('pointercancel', stopResize)
    if (storageKey) localStorage.setItem(storageKey, String(width.value))
  }

  function startResize(e) {
    isResizing.value = true
    startX = e.clientX
    startWidth = width.value
    document.addEventListener('pointermove', onPointerMove)
    document.addEventListener('pointerup', stopResize)
    // Without this, an interrupted gesture (the browser dropping/canceling
    // the pointer sequence — e.g. a fast move outside the tracked area, an
    // OS-level trackpad tap-and-drag gesture ending abnormally, or losing
    // window focus mid-drag — never fires 'pointerup', leaving isResizing
    // stuck true and these document-level listeners permanently attached.
    // Any later mouse movement anywhere on the page then keeps resizing the
    // panel from that stale startX/startWidth, with no click involved at all
    // (issue feedback: "moving the mouse near the divider moves it without
    // clicking; moving fast makes it stop, probably because the browser
    // can't keep up" — exactly this failure mode).
    document.addEventListener('pointercancel', stopResize)
    e.preventDefault()
  }

  onBeforeUnmount(stopResize)

  return { width, isResizing, startResize }
}
