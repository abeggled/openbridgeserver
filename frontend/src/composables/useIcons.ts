import { ref } from 'vue'
import { icons as iconsApi } from '@/api/client'

// Module-level shared state – one fetch for all components
const iconNames = ref<string[]>([])
const svgCache: Record<string, string> = {}  // name → normalised SVG string
let listPromise: Promise<void> | null = null
const BLOCKED_URL_SCHEMES = ['javascript:', 'data:', 'http:', 'https:', 'vbscript:', 'file:']
const URL_FUNCTION_ATTRIBUTES = new Set([
  'fill',
  'stroke',
  'filter',
  'clip-path',
  'mask',
  'marker-start',
  'marker-mid',
  'marker-end',
  'cursor',
])

function stripAsciiControlsAndWhitespace(raw: string): string {
  return raw.toLowerCase().replace(/[\u0000-\u0020\u007f-\u009f]+/g, '')
}

function isBlockedUrlReference(rawValue: string): boolean {
  const normalized = stripAsciiControlsAndWhitespace(rawValue)
  return normalized.startsWith('//') || BLOCKED_URL_SCHEMES.some((scheme) => normalized.startsWith(scheme))
}

function hasBlockedCssUrlFunction(value: string): boolean {
  for (const match of value.matchAll(/url\(([^)]*)\)/gi)) {
    const rawRef = (match[1] || '').trim().replace(/^['"]|['"]$/g, '')
    if (isBlockedUrlReference(rawRef)) return true
  }
  return false
}

function sanitizeSvg(raw: string): string {
  const parser = new DOMParser()
  const doc = parser.parseFromString(raw, 'image/svg+xml')
  const svg = doc.documentElement

  if (!svg || svg.tagName.toLowerCase() !== 'svg') return ''

  // Drop executable, externally embeddable, or dynamic mutation content.
  doc.querySelectorAll('script,style,foreignObject,iframe,object,embed,audio,video,animate,set,animateMotion,animateTransform').forEach((el) => el.remove())

  for (const el of [svg, ...Array.from(doc.querySelectorAll('*'))]) {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase()
      const localName = (attr.localName || attr.name).toLowerCase()
      const lowerValue = attr.value.toLowerCase()
      const normalizedValue = stripAsciiControlsAndWhitespace(attr.value)

      if (el === svg && (name === 'width' || name === 'height')) {
        el.removeAttribute(attr.name)
        continue
      }

      if (name.startsWith('on')) {
        el.removeAttribute(attr.name)
        continue
      }

      if (name === 'style' && (lowerValue.includes('url(') || lowerValue.includes('@import'))) {
        el.removeAttribute(attr.name)
        continue
      }

      if ((localName === 'href' || localName === 'src') && (
        normalizedValue.startsWith('//') ||
        BLOCKED_URL_SCHEMES.some((scheme) => normalizedValue.startsWith(scheme))
      )) {
        el.removeAttribute(attr.name)
        continue
      }

      if (URL_FUNCTION_ATTRIBUTES.has(localName) && hasBlockedCssUrlFunction(attr.value)) {
        el.removeAttribute(attr.name)
      }
    }
  }

  return svg.outerHTML
}

export function useIcons() {
  function loadList(): Promise<void> {
    if (listPromise) return listPromise
    listPromise = iconsApi
      .list()
      .then(({ icons }) => {
        // Populate cache from the list response (content is already included)
        for (const icon of icons) {
          svgCache[icon.name] = sanitizeSvg(icon.content)
        }
        iconNames.value = icons.map((i) => i.name)
      })
      .catch(() => {
        // Icons endpoint requires auth — treat as empty list in unauthenticated context
        listPromise = null  // allow retry after login
        iconNames.value = []
      })
    return listPromise
  }

  async function getSvg(name: string): Promise<string> {
    if (name in svgCache) return svgCache[name]
    // Cache miss — load the full list (which includes content for all icons)
    await loadList()
    return svgCache[name] ?? ''
  }

  /** Returns true if the icon value refers to an imported SVG icon */
  function isSvgIcon(value: string | null | undefined): value is string {
    return typeof value === 'string' && value.startsWith('svg:')
  }

  /** Extracts the icon name from a `svg:{name}` value */
  function svgIconName(value: string): string {
    return value.slice(4)
  }

  return { iconNames, loadList, getSvg, isSvgIcon, svgIconName }
}
