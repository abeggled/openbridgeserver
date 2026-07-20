// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest'
import { useResizablePanel } from './useResizablePanel'

function firePointer(type: string, clientX: number) {
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
    startResize({ clientX: 500, preventDefault: () => {} } as PointerEvent)
    expect(isResizing.value).toBe(true)

    firePointer('pointermove', 400)
    expect(width.value).toBe(400)

    firePointer('pointerup', 400)
    expect(isResizing.value).toBe(false)
  })

  it('narrows the panel when the pointer moves right of the drag start', () => {
    const { width, startResize } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} } as PointerEvent)
    firePointer('pointermove', 550)
    expect(width.value).toBe(250)
  })

  it('clamps live resize to min and max while dragging', () => {
    const { width, startResize } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} } as PointerEvent)

    firePointer('pointermove', -10000)
    expect(width.value).toBe(500)

    firePointer('pointermove', 10000)
    expect(width.value).toBe(200)
  })

  it('ignores pointermove events once resizing has stopped', () => {
    const { width, startResize } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} } as PointerEvent)
    firePointer('pointerup', 500)

    firePointer('pointermove', 100)
    expect(width.value).toBe(300)
  })

  it('persists the final width to localStorage on pointerup when storageKey is set', () => {
    const { startResize } = useResizablePanel({ storageKey: 'obs.test.panel', defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} } as PointerEvent)
    firePointer('pointermove', 420)
    firePointer('pointerup', 420)

    expect(localStorage.getItem('obs.test.panel')).toBe('380')
  })

  it('does not touch localStorage when no storageKey is given', () => {
    const { startResize } = useResizablePanel({ defaultWidth: 300, min: 200, max: 500 })
    startResize({ clientX: 500, preventDefault: () => {} } as PointerEvent)
    firePointer('pointerup', 500)
    expect(localStorage.length).toBe(0)
  })

  it('calls preventDefault on drag start to avoid text-selection artifacts', () => {
    const { startResize } = useResizablePanel({ defaultWidth: 300 })
    let prevented = false
    startResize({ clientX: 500, preventDefault: () => { prevented = true } } as unknown as PointerEvent)
    expect(prevented).toBe(true)
  })

  it('a second pointerup (e.g. duplicate event) is a no-op and does not throw', () => {
    const { startResize, isResizing } = useResizablePanel({ storageKey: 'obs.test.panel', defaultWidth: 300 })
    startResize({ clientX: 500, preventDefault: () => {} } as PointerEvent)
    firePointer('pointerup', 500)
    expect(isResizing.value).toBe(false)
    expect(() => firePointer('pointerup', 500)).not.toThrow()
  })
})
