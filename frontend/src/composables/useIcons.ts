import { ref } from 'vue'
import { icons as iconsApi } from '@/api/client'

// Module-level shared state – one fetch for all components
const iconNames = ref<string[]>([])
const svgCache: Record<string, string> = {}  // name → normalised SVG string
let listPromise: Promise<void> | null = null

function sanitizeSvg(raw: string): string {
  const parser = new DOMParser()
  const doc = parser.parseFromString(raw, 'image/svg+xml')
  const svg = doc.documentElement
  if (!svg || svg.tagName.toLowerCase() !== 'svg') return ''

  // Remove executable or HTML-capable elements
  doc.querySelectorAll('script, foreignObject').forEach((el) => el.remove())

  // Remove dangerous attributes and fixed dimensions
  for (const el of Array.from(doc.querySelectorAll('*'))) {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase()
      const value = attr.value.trim().toLowerCase()

      if (name === 'width' || name === 'height') {
        el.removeAttribute(attr.name)
        continue
      }

      if (name.startsWith('on')) {
        el.removeAttribute(attr.name)
        continue
      }

      if ((name === 'href' || name === 'xlink:href') && value.startsWith('javascript:')) {
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
