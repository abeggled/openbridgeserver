/**
 * Pure coordinate math for the Grundriss widget.
 *
 * The widget renders the floorplan image inside a CSS-rotated inner div
 * (see Widget.vue's innerStyle). Mini-widgets and labels live outside that
 * div and are positioned via imageToScreen(). The config editor uses the
 * inverse screenToImage() so mouse drags always update mw.x / mw.y in the
 * correct unrotated image coordinate space regardless of cfg.rotation.
 */

export type Rotation = 0 | 90 | 180 | 270

export interface LayoutParams {
  containerW: number  // screen width  of the widget / canvas container
  containerH: number  // screen height of the widget / canvas container
  naturalW:   number  // image natural width  (unrotated)
  naturalH:   number  // image natural height (unrotated)
  rotation:   Rotation
}

function layoutInternals(p: LayoutParams) {
  const { containerW: W, containerH: H, naturalW: NW, naturalH: NH, rotation: r } = p
  // For 90°/270° the rotated content's effective dimensions are swapped.
  const innerW = (r === 90 || r === 270) ? H : W
  const innerH = (r === 90 || r === 270) ? W : H
  const s  = Math.min(innerW / NW, innerH / NH)  // object-fit: contain scale
  const ox = (innerW - NW * s) / 2               // letterbox X offset (inner-div space)
  const oy = (innerH - NH * s) / 2               // letterbox Y offset
  return { W, H, NW, NH, s, ox, oy, r }
}

/**
 * Map unrotated image pixels → container-relative screen pixels.
 *
 * Mirrors the closed-form result of Widget.vue's CSS `rotate(Ndeg)` inner div +
 * object-fit:contain letterboxing.
 */
export function imageToScreen(px: number, py: number, p: LayoutParams): [number, number] {
  const { W, H, s, ox, oy, r } = layoutInternals(p)
  switch (r) {
    case 90:  return [W - oy - py * s, ox + px * s]
    case 180: return [W - ox - px * s, H - oy - py * s]
    case 270: return [oy + py * s,     H - ox - px * s]
    default:  return [ox + px * s,     oy + py * s]
  }
}

/**
 * Map container-relative screen pixels → unrotated image pixels.
 *
 * Exact algebraic inverse of imageToScreen(). Used by Config.vue's drag handler
 * so that dragging a mini-widget handle in any rotation updates mw.x / mw.y
 * in unrotated image space. Result is clamped to [0..naturalW] × [0..naturalH].
 */
export function screenToImage(sx: number, sy: number, p: LayoutParams): [number, number] {
  const { W, H, NW, NH, s, ox, oy, r } = layoutInternals(p)
  let px: number, py: number
  switch (r) {
    case 90:  px = (sy - ox) / s;     py = (W - oy - sx) / s; break
    case 180: px = (W - ox - sx) / s; py = (H - oy - sy) / s; break
    case 270: px = (H - ox - sy) / s; py = (sx - oy) / s;     break
    default:  px = (sx - ox) / s;     py = (sy - oy) / s;     break
  }
  return [Math.max(0, Math.min(NW, px)), Math.max(0, Math.min(NH, py))]
}
