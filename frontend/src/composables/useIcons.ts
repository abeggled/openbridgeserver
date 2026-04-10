import { ref } from 'vue'
import { icons as iconsApi } from '@/api/client'

// Module-level shared state – one fetch for all components
const iconNames = ref<string[]>([])
const svgCache: Record<string, string> = {}  // name → normalised SVG string
let listPromise: Promise<void> | null = null

function normalizeSvg(raw: string): string {
  // Strip fixed width/height from root <svg> so CSS can control the size
  return raw.replace(/<svg([^>]*)>/, (_, attrs: string) => {
    const cleaned = attrs
      .replace(/\s+width="[^"]*"/g, '')
      .replace(/\s+height="[^"]*"/g, '')
    return `<svg${cleaned}>`
  })
}

export function useIcons() {
  function loadList(): Promise<void> {
    if (listPromise) return listPromise
    listPromise = iconsApi
      .list()
      .then(({ icons }) => {
        // Populate cache from the list response (content is already included)
        for (const icon of icons) {
          svgCache[icon.name] = normalizeSvg(icon.content)
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
