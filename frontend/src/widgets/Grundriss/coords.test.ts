import { describe, it, expect } from 'vitest'
import { imageToScreen, screenToImage } from './coords'
import type { LayoutParams, Rotation } from './coords'

// ── Fixtures ──────────────────────────────────────────────────────────────────
// Landscape container (800×600), landscape image (1920×1080).
// For rotation=0 and rotation=180 the container is wider than tall —
// the image is letterboxed top/bottom.
// For rotation=90 and rotation=270 the inner dimensions are swapped (600×800),
// making the image letterboxed left/right.

const CONTAINER_W = 800
const CONTAINER_H = 600
const NATURAL_W   = 1920
const NATURAL_H   = 1080

function params(rotation: Rotation): LayoutParams {
  return { containerW: CONTAINER_W, containerH: CONTAINER_H, naturalW: NATURAL_W, naturalH: NATURAL_H, rotation }
}

// Representative image-space points: corners + center
const IMAGE_POINTS: Array<[number, number]> = [
  [0,    0   ],   // top-left
  [1920, 0   ],   // top-right
  [0,    1080],   // bottom-left
  [1920, 1080],   // bottom-right
  [960,  540 ],   // center
  [480,  270 ],   // upper quarter
  [1,    1   ],   // near origin
]

const ROTATIONS: Rotation[] = [0, 90, 180, 270]

// ── Round-trip tests ──────────────────────────────────────────────────────────
// The fundamental invariant: screenToImage( imageToScreen(p) ) === p
// for any point inside the image and any rotation.

describe('imageToScreen / screenToImage — round-trip', () => {
  for (const r of ROTATIONS) {
    describe(`rotation ${r}°`, () => {
      for (const [px, py] of IMAGE_POINTS) {
        it(`(${px}, ${py}) → screen → back to image`, () => {
          const p = params(r)
          const [sx, sy] = imageToScreen(px, py, p)
          const [rx, ry] = screenToImage(sx, sy, p)
          expect(rx).toBeCloseTo(px, 4)
          expect(ry).toBeCloseTo(py, 4)
        })
      }
    })
  }
})

// ── Specific screen positions ─────────────────────────────────────────────────
// Verify the actual screen coordinates produced by imageToScreen() for
// known inputs, so a refactor can't silently flip the formulas.

describe('imageToScreen — known screen positions', () => {
  it('rotation 0: image top-left maps to letterbox offset', () => {
    // innerW=800, innerH=600, scale=min(800/1920,600/1080)=min(0.4167,0.5556)=0.4167
    // ox=(800-1920*0.4167)/2=0, oy=(600-1080*0.4167)/2≈74.97
    const p = params(0)
    const s  = Math.min(CONTAINER_W / NATURAL_W, CONTAINER_H / NATURAL_H)
    const ox = (CONTAINER_W - NATURAL_W * s) / 2
    const oy = (CONTAINER_H - NATURAL_H * s) / 2
    const [sx, sy] = imageToScreen(0, 0, p)
    expect(sx).toBeCloseTo(ox, 3)
    expect(sy).toBeCloseTo(oy, 3)
  })

  it('rotation 0: image center maps to screen center', () => {
    const p = params(0)
    const [sx, sy] = imageToScreen(NATURAL_W / 2, NATURAL_H / 2, p)
    expect(sx).toBeCloseTo(CONTAINER_W / 2, 3)
    expect(sy).toBeCloseTo(CONTAINER_H / 2, 3)
  })

  it('rotation 180: image top-left maps to opposite of rotation-0 bottom-right', () => {
    // imageToScreen(0,0) for 180° = [W - ox - 0, H - oy - 0]
    const p0   = params(0)
    const p180 = params(180)
    const [sx0, sy0] = imageToScreen(NATURAL_W, NATURAL_H, p0)   // bottom-right at 0°
    const [sx1, sy1] = imageToScreen(0, 0, p180)                  // top-left at 180°
    expect(sx1).toBeCloseTo(sx0, 3)
    expect(sy1).toBeCloseTo(sy0, 3)
  })

  it('rotation 90: image center still maps to screen center', () => {
    // Rotating 90° around the image centre keeps the centre at the screen centre.
    const p = params(90)
    const [sx, sy] = imageToScreen(NATURAL_W / 2, NATURAL_H / 2, p)
    expect(sx).toBeCloseTo(CONTAINER_W / 2, 3)
    expect(sy).toBeCloseTo(CONTAINER_H / 2, 3)
  })

  it('rotation 270: image center still maps to screen center', () => {
    const p = params(270)
    const [sx, sy] = imageToScreen(NATURAL_W / 2, NATURAL_H / 2, p)
    expect(sx).toBeCloseTo(CONTAINER_W / 2, 3)
    expect(sy).toBeCloseTo(CONTAINER_H / 2, 3)
  })
})

// ── Symmetry / antisymmetry ───────────────────────────────────────────────────
// rotation 180° is equivalent to reflecting both axes through the screen centre.

describe('imageToScreen — 180° symmetry', () => {
  it('p and its antipodal point sum to the screen centre (×2)', () => {
    const p = params(180)
    const testPoints: Array<[number, number]> = [[100, 200], [960, 540], [0, 0]]
    for (const [px, py] of testPoints) {
      const [sx,  sy ] = imageToScreen(px,              py,              p)
      const [sx2, sy2] = imageToScreen(NATURAL_W - px,  NATURAL_H - py,  p)
      expect(sx + sx2).toBeCloseTo(CONTAINER_W, 3)
      expect(sy + sy2).toBeCloseTo(CONTAINER_H, 3)
    }
  })
})

// ── Clamping ──────────────────────────────────────────────────────────────────
// screenToImage must clamp to valid image space even for out-of-bounds screen coords.

describe('screenToImage — clamping', () => {
  for (const r of ROTATIONS) {
    it(`rotation ${r}°: very negative screen coords clamp to (0, 0)`, () => {
      const [px, py] = screenToImage(-9999, -9999, params(r))
      expect(px).toBeGreaterThanOrEqual(0)
      expect(py).toBeGreaterThanOrEqual(0)
    })

    it(`rotation ${r}°: very large screen coords clamp to (naturalW, naturalH)`, () => {
      const [px, py] = screenToImage(9999, 9999, params(r))
      expect(px).toBeLessThanOrEqual(NATURAL_W)
      expect(py).toBeLessThanOrEqual(NATURAL_H)
    })
  }
})

// ── The bug this fixes ────────────────────────────────────────────────────────
// Before the fix, Config.vue used the unrotated SVG formula for getImageCoords
// (equivalent to rotation=0 always). Verify that the correct rotation-aware
// inverse differs from the rotation=0 result for non-zero rotations.

describe('regression: rotation-aware inverse differs from naïve rotation-0 formula', () => {
  const naiveScreenToImage = (sx: number, sy: number, p: LayoutParams): [number, number] => {
    // The old formula: always uses unrotated scale (the bug)
    const scale = Math.min(p.containerW / p.naturalW, p.containerH / p.naturalH)
    const offX  = (p.containerW - p.naturalW * scale) / 2
    const offY  = (p.containerH - p.naturalH * scale) / 2
    return [
      Math.max(0, Math.min(p.naturalW, (sx - offX) / scale)),
      Math.max(0, Math.min(p.naturalH, (sy - offY) / scale)),
    ]
  }

  for (const r of ([90, 180, 270] as Rotation[])) {
    it(`rotation ${r}°: correct inverse ≠ naïve rotation-0 formula for off-centre point`, () => {
      const p  = params(r)
      const pt: [number, number] = [300, 200]    // an off-centre image point
      const [sx, sy] = imageToScreen(...pt, p)   // ground-truth screen position

      const [cx, cy] = screenToImage(sx, sy, p)  // correct inverse
      const [nx, ny] = naiveScreenToImage(sx, sy, p)  // the old buggy formula

      // The correct result round-trips back to the original point.
      expect(cx).toBeCloseTo(pt[0], 3)
      expect(cy).toBeCloseTo(pt[1], 3)

      // The naïve formula returns a wrong result (at least one coordinate differs).
      const naiveIsWrong = Math.abs(nx - pt[0]) > 0.5 || Math.abs(ny - pt[1]) > 0.5
      expect(naiveIsWrong).toBe(true)
    })
  }
})
