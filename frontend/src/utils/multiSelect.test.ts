import { describe, it, expect } from 'vitest'
import { rectFromPoints, widgetsInRect, computeGroupMove, type GridWidgetLike } from './multiSelect'

describe('rectFromPoints', () => {
  it('normalizes a drag dragged down-right', () => {
    expect(rectFromPoints(10, 20, 110, 220)).toEqual({ left: 10, top: 20, width: 100, height: 200 })
  })

  it('normalizes a drag dragged up-left (reversed direction)', () => {
    expect(rectFromPoints(110, 220, 10, 20)).toEqual({ left: 10, top: 20, width: 100, height: 200 })
  })

  it('normalizes a drag with mixed directions', () => {
    expect(rectFromPoints(10, 220, 110, 20)).toEqual({ left: 10, top: 20, width: 100, height: 200 })
  })

  it('returns a zero-size rect for a click without movement', () => {
    expect(rectFromPoints(50, 50, 50, 50)).toEqual({ left: 50, top: 50, width: 0, height: 0 })
  })
})

describe('widgetsInRect', () => {
  const cellW = 80
  const cellH = 40
  const widgets: GridWidgetLike[] = [
    { id: 'a', x: 0, y: 0, w: 2, h: 2 },  // px: 0-160, 0-80
    { id: 'b', x: 3, y: 0, w: 1, h: 1 },  // px: 240-320, 0-40
    { id: 'c', x: 0, y: 5, w: 2, h: 2 },  // px: 0-160, 200-280 (far below)
  ]

  it('selects widgets fully inside the rect', () => {
    const rect = { left: 0, top: 0, width: 400, height: 100 }
    expect(widgetsInRect(widgets, rect, cellW, cellH)).toEqual(new Set(['a', 'b']))
  })

  it('selects widgets that only partially intersect the rect', () => {
    // rect only overlaps the right sliver of widget "a" and none of "b"
    const rect = { left: 150, top: 0, width: 20, height: 20 }
    expect(widgetsInRect(widgets, rect, cellW, cellH)).toEqual(new Set(['a']))
  })

  it('excludes widgets entirely outside the rect', () => {
    const rect = { left: 0, top: 0, width: 400, height: 100 }
    expect(widgetsInRect(widgets, rect, cellW, cellH).has('c')).toBe(false)
  })

  it('excludes widgets that only touch the rect edge (no overlap area)', () => {
    // rect ends exactly where widget "b" begins (240px) — touching, not overlapping
    const rect = { left: 0, top: 0, width: 240, height: 40 }
    expect(widgetsInRect(widgets, rect, cellW, cellH).has('b')).toBe(false)
  })

  it('returns an empty set for a zero-size rect over empty canvas (plain click, no drag)', () => {
    const rect = { left: 500, top: 500, width: 0, height: 0 }
    expect(widgetsInRect(widgets, rect, cellW, cellH).size).toBe(0)
  })

  it('treats a zero-size rect landing inside a widget as a hit (point-in-widget)', () => {
    const rect = { left: 10, top: 10, width: 0, height: 0 }
    expect(widgetsInRect(widgets, rect, cellW, cellH)).toEqual(new Set(['a']))
  })

  it('returns an empty set when there are no widgets', () => {
    const rect = { left: 0, top: 0, width: 1000, height: 1000 }
    expect(widgetsInRect([], rect, cellW, cellH).size).toBe(0)
  })
})

describe('computeGroupMove', () => {
  it('applies the same delta to every widget in the group', () => {
    const start = new Map([
      ['a', { x: 0, y: 0 }],
      ['b', { x: 3, y: 2 }],
    ])
    const widthById = new Map([['a', 2], ['b', 1]])
    const result = computeGroupMove(start, 1, 1, widthById, 12)
    expect(result.get('a')).toEqual({ x: 1, y: 1 })
    expect(result.get('b')).toEqual({ x: 4, y: 3 })
  })

  it('clamps each widget independently to the left/top canvas edge', () => {
    const start = new Map([
      ['a', { x: 0, y: 0 }],
      ['b', { x: 5, y: 5 }],
    ])
    const widthById = new Map([['a', 2], ['b', 2]])
    // dx/dy would push "a" off the negative edge, "b" stays within bounds
    const result = computeGroupMove(start, -3, -3, widthById, 12)
    expect(result.get('a')).toEqual({ x: 0, y: 0 })
    expect(result.get('b')).toEqual({ x: 2, y: 2 })
  })

  it('clamps each widget independently to the right canvas edge based on its own width', () => {
    const start = new Map([
      ['narrow', { x: 5, y: 0 }],
      ['wide', { x: 5, y: 0 }],
    ])
    const widthById = new Map([['narrow', 1], ['wide', 4]])
    // dx pushes both far right; "narrow" (w=1) can reach col 11, "wide" (w=4) only col 8
    const result = computeGroupMove(start, 20, 0, widthById, 12)
    expect(result.get('narrow')).toEqual({ x: 11, y: 0 })
    expect(result.get('wide')).toEqual({ x: 8, y: 0 })
  })

  it('falls back to width 1 when a widget id is missing from widthById', () => {
    const start = new Map([['ghost', { x: 5, y: 0 }]])
    const result = computeGroupMove(start, 10, 0, new Map(), 12)
    expect(result.get('ghost')).toEqual({ x: 11, y: 0 })
  })

  it('returns an empty map for an empty selection', () => {
    const result = computeGroupMove(new Map(), 5, 5, new Map(), 12)
    expect(result.size).toBe(0)
  })

  it('leaves positions unchanged for a zero delta', () => {
    const start = new Map([['a', { x: 4, y: 4 }]])
    const widthById = new Map([['a', 2]])
    const result = computeGroupMove(start, 0, 0, widthById, 12)
    expect(result.get('a')).toEqual({ x: 4, y: 4 })
  })
})
