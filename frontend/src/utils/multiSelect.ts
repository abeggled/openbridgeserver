/** Pure selection-geometry helpers for the Visu editor's multiselect (issue #1036). */

export interface RectPx {
  left: number
  top: number
  width: number
  height: number
}

/** Normalizes a drag-start/current point pair (which may go in any direction) into a top-left rect. */
export function rectFromPoints(startX: number, startY: number, curX: number, curY: number): RectPx {
  return {
    left: Math.min(startX, curX),
    top: Math.min(startY, curY),
    width: Math.abs(curX - startX),
    height: Math.abs(curY - startY),
  }
}

export interface GridWidgetLike {
  id: string
  x: number
  y: number
  w: number
  h: number
}

/** Returns the ids of every widget whose pixel bounds intersect the marquee rect (grid units × cell size). */
export function widgetsInRect(widgets: GridWidgetLike[], rect: RectPx, cellW: number, cellH: number): Set<string> {
  const hit = new Set<string>()
  for (const w of widgets) {
    const wx = w.x * cellW
    const wy = w.y * cellH
    const ww = w.w * cellW
    const wh = w.h * cellH
    const intersects = wx < rect.left + rect.width && wx + ww > rect.left && wy < rect.top + rect.height && wy + wh > rect.top
    if (intersects) hit.add(w.id)
  }
  return hit
}

/**
 * Applies the same (dx, dy) grid-cell delta to every widget's start position, each clamped
 * independently to stay within [0, cols - width] / [0, ∞) — mirrors the single-widget clamp
 * used by the pre-multiselect drag code, applied per widget in the group.
 */
export function computeGroupMove(
  startPositions: Map<string, { x: number; y: number }>,
  dx: number,
  dy: number,
  widthById: Map<string, number>,
  cols: number,
): Map<string, { x: number; y: number }> {
  const next = new Map<string, { x: number; y: number }>()
  for (const [id, start] of startPositions) {
    const w = widthById.get(id) ?? 1
    next.set(id, {
      x: Math.max(0, Math.min(cols - w, start.x + dx)),
      y: Math.max(0, start.y + dy),
    })
  }
  return next
}
