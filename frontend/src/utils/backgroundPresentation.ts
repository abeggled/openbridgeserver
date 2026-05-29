export type BackgroundFitMode = 'cover' | 'contain' | 'width' | 'height' | 'stretch' | 'tile'
export type BackgroundPositionMode =
  | 'center'
  | 'top'
  | 'bottom'
  | 'left'
  | 'right'
  | 'top-left'
  | 'top-right'
  | 'bottom-left'
  | 'bottom-right'

export interface ParsedBackgroundPresentation {
  kind: 'none' | 'catalog' | 'raw'
  raw: string | null
  catalogName: string | null
  fit: BackgroundFitMode
  position: BackgroundPositionMode
}

const FIT_MODES = new Set<BackgroundFitMode>(['cover', 'contain', 'width', 'height', 'stretch', 'tile'])
const POS_MODES = new Set<BackgroundPositionMode>([
  'center', 'top', 'bottom', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right',
])

function parseToken(token: string, key: 'fit' | 'pos'): string | null {
  const prefix = `${key}:`
  if (!token.startsWith(prefix)) return null
  return token.slice(prefix.length)
}

export function parseBackgroundPresentation(value: string | null | undefined): ParsedBackgroundPresentation {
  const raw = value ?? null
  if (!raw) {
    return { kind: 'none', raw: null, catalogName: null, fit: 'cover', position: 'center' }
  }

  if (raw.startsWith('catalog:')) {
    const [head, ...meta] = raw.split('|')
    const name = head.slice('catalog:'.length) || null
    let fit: BackgroundFitMode = 'cover'
    let position: BackgroundPositionMode = 'center'

    for (const token of meta) {
      const fitVal = parseToken(token, 'fit')
      if (fitVal && FIT_MODES.has(fitVal as BackgroundFitMode)) {
        fit = fitVal as BackgroundFitMode
        continue
      }
      const posVal = parseToken(token, 'pos')
      if (posVal && POS_MODES.has(posVal as BackgroundPositionMode)) {
        position = posVal as BackgroundPositionMode
      }
    }

    return { kind: 'catalog', raw, catalogName: name, fit, position }
  }

  return { kind: 'raw', raw, catalogName: null, fit: 'cover', position: 'center' }
}

export function serializeCatalogBackground(name: string, fit: BackgroundFitMode, position: BackgroundPositionMode): string {
  return `catalog:${name}|fit:${fit}|pos:${position}`
}

export function cssBackgroundSize(fit: BackgroundFitMode): string {
  switch (fit) {
    case 'contain':
      return 'contain'
    case 'width':
      return '100% auto'
    case 'height':
      return 'auto 100%'
    case 'stretch':
      return '100% 100%'
    case 'tile':
      return 'auto'
    case 'cover':
    default:
      return 'cover'
  }
}

export function cssBackgroundRepeat(fit: BackgroundFitMode): string {
  return fit === 'tile' ? 'repeat' : 'no-repeat'
}

export function cssBackgroundPosition(position: BackgroundPositionMode): string {
  switch (position) {
    case 'top':
      return 'top center'
    case 'bottom':
      return 'bottom center'
    case 'left':
      return 'center left'
    case 'right':
      return 'center right'
    case 'top-left':
      return 'top left'
    case 'top-right':
      return 'top right'
    case 'bottom-left':
      return 'bottom left'
    case 'bottom-right':
      return 'bottom right'
    case 'center':
    default:
      return 'center'
  }
}
