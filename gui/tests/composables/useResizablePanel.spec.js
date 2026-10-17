/**
 * Tests for the useResizablePanel composable (issue #1034).
 *
 * Drag-to-resize for a right-anchored side panel: a handle on the panel's
 * left edge is grabbed via `startResize(pointerdown)`, then `width` tracks
 * `pointermove` until `pointerup`, clamped to [min, max] and persisted to
 * localStorage under `storageKey`.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useResizablePanel } from '@/composables/useResizablePanel'

function firePointer(type, clientX) {
  document.dispatchEvent(new MouseEvent(type, { clientX, bubbles: true }))
}

describe('useResizablePanel', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('starts at defaultWidth when nothing is stored', () => {
    const { width } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    expect(width.value).toBe(300)
  })

  it('clamps a defaultWidth below min when nothing is stored (e.g. computed from a narrow viewport)', () => {
    const { width } = useResizablePanel({ defaultWidth: 156, min: 320, max: 960 })
    expect(width.value).toBe(320)
  })

  it('clamps a defaultWidth above max when nothing is stored', () => {
    const { width } = useResizablePanel({ defaultWidth: 5000, min: 200, max: 500 })
    expect(width.value).toBe(500)
  })

  it('restores a previously persisted width from localStorage, clamped to min/max', () => {
    localStorage.setItem('obs.test.panel', '400')
    const { width } = useResizablePanel({ storageKey: 'obs.test.panel', defaultWidth: 288, min: 240, max: 640 })
    expect(width.value).toBe(400)
  })

  it('clamps a stored width above max down to max', () => {
    localStorage.setItem('obs.test.panel', '9999')
    const { width } = useResizablePanel({ storageKey: 'obs.test.panel', min: 240, max: 640 })
    expect(width.value).toBe(640)
  })

  it('ignores an invalid stored value and falls back to defaultWidth', () => {
    localStorage.setItem('obs.test.panel', 'not-a-number')
    const { width } = useResizablePanel({ storageKey: 'obs.test.panel', defaultWidth: 288 })
    expect(width.value).toBe(288)
  })

  it('widens the panel when the pointer moves left of the drag start', () => {
    const { width, isResizing, startResize } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} })
    expect(isResizing.value).toBe(true)

    firePointer('pointermove', 400) // moved 100px left → +100 width
    expect(width.value).toBe(400)

    firePointer('pointerup', 400)
    expect(isResizing.value).toBe(false)
  })

  it('narrows the panel when the pointer moves right of the drag start', () => {
    const { width, startResize } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} })
    firePointer('pointermove', 550) // moved 50px right → -50 width
    expect(width.value).toBe(250)
  })

  it('clamps live resize to min and max while dragging', () => {
    const { width, startResize } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} })

    firePointer('pointermove', -10000) // far left → would exceed max
    expect(width.value).toBe(500)

    firePointer('pointermove', 10000) // far right → would go below min
    expect(width.value).toBe(200)
  })

  it('ignores pointermove events once resizing has stopped', () => {
    const { width, startResize } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} })
    firePointer('pointerup', 500)

    firePointer('pointermove', 100)
    expect(width.value).toBe(300)
  })

  it('persists the final width to localStorage on pointerup when storageKey is set', () => {
    const { startResize } = useResizablePanel({ storageKey: 'obs.test.panel', defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} })
    firePointer('pointermove', 420)
    firePointer('pointerup', 420)

    expect(localStorage.getItem('obs.test.panel')).toBe('380')
  })

  it('does not touch localStorage when no storageKey is given', () => {
    const { startResize } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} })
    firePointer('pointerup', 500)
    expect(localStorage.length).toBe(0)
  })

  it('calls preventDefault on drag start to avoid text-selection artifacts', () => {
    const { startResize } = useResizablePanel({ defaultWidth: 300 })
    let prevented = false
    startResize({ clientX: 500, preventDefault: () => { prevented = true } })
    expect(prevented).toBe(true)
  })

  it('a second stopResize() call (e.g. duplicate pointerup) is a no-op', () => {
    const { startResize, isResizing } = useResizablePanel({ storageKey: 'obs.test.panel', defaultWidth: 300 })
    startResize({ clientX: 500, preventDefault: () => {} })
    firePointer('pointerup', 500)
    expect(isResizing.value).toBe(false)
    // Second pointerup after listeners were already removed must not throw.
    expect(() => firePointer('pointerup', 500)).not.toThrow()
  })

  it('stops resizing on pointercancel, not just pointerup (issue feedback: an interrupted drag gesture — e.g. a fast move the browser can\'t track, or an OS trackpad tap-and-drag ending abnormally — never fired pointerup, leaving the panel resizing from unrelated later mouse movement anywhere on the page with no click involved)', () => {
    const { width, isResizing, startResize } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} })
    firePointer('pointermove', 400)
    expect(width.value).toBe(400)

    firePointer('pointercancel', 400)
    expect(isResizing.value).toBe(false)

    // Movement after the cancel must be inert — no click, no resize.
    firePointer('pointermove', 100)
    expect(width.value).toBe(400)
  })

  it('persists the width reached before a pointercancel, same as a normal pointerup', () => {
    const { startResize } = useResizablePanel({ storageKey: 'obs.test.panel', defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} })
    firePointer('pointermove', 420)
    firePointer('pointercancel', 420)

    expect(localStorage.getItem('obs.test.panel')).toBe('380')
  })
})
