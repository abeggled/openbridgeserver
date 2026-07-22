import { ref, onBeforeUnmount } from 'vue'

export interface ResizablePanelOptions {
  storageKey?: string
  defaultWidth?: number
  min?: number
  max?: number
}

/**
 * Drag-to-resize for a side panel anchored to the right edge of its container.
 * The handle sits on the panel's left edge; dragging it left widens the panel.
 * Width persists per `storageKey` in localStorage across sessions.
 */
export function useResizablePanel(options: ResizablePanelOptions = {}) {
  const { storageKey, defaultWidth = 288, min = 240, max = 640 } = options

  const stored = storageKey ? Number(localStorage.getItem(storageKey)) : NaN
  const clamp = (w: number) => Math.min(max, Math.max(min, w))
  const width = ref(Number.isFinite(stored) && stored > 0 ? clamp(stored) : defaultWidth)
  const isResizing = ref(false)

  let startX = 0
  let startWidth = 0

  function onPointerMove(e: PointerEvent) {
    width.value = clamp(startWidth + (startX - e.clientX))
  }

  function stopResize() {
    if (!isResizing.value) return
    isResizing.value = false
    document.removeEventListener('pointermove', onPointerMove)
    document.removeEventListener('pointerup', stopResize)
    if (storageKey) localStorage.setItem(storageKey, String(width.value))
  }

  function startResize(e: PointerEvent) {
    isResizing.value = true
    startX = e.clientX
    startWidth = width.value
    document.addEventListener('pointermove', onPointerMove)
    document.addEventListener('pointerup', stopResize)
    e.preventDefault()
  }

  onBeforeUnmount(stopResize)

  return { width, isResizing, startResize }
}
