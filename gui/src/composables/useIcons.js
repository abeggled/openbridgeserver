/**
 * useIcons — shared composable for the gui app
 * Fetches the icon library once per module lifecycle and caches SVG content.
 * Icons are stored as { name, size, content } in GET /api/v1/icons/.
 */
import { ref } from 'vue'
import { iconsApi } from '@/api/client'

const iconNames = ref([])
const svgCache  = {}   // name → normalised SVG string
let listPromise = null

function normalizeSvg(raw) {
  return raw.replace(/<svg([^>]*)>/, (_, attrs) => {
    const cleaned = attrs
      .replace(/\s+width="[^"]*"/g, '')
      .replace(/\s+height="[^"]*"/g, '')
    return `<svg${cleaned}>`
  })
}

export function useIcons() {
  function loadList() {
    if (listPromise) return listPromise
    listPromise = iconsApi.list()
      .then(({ data }) => {
        for (const icon of data.icons ?? []) {
          svgCache[icon.name] = normalizeSvg(icon.content)
        }
        iconNames.value = (data.icons ?? []).map(i => i.name)
      })
      .catch(() => {
        listPromise = null
        iconNames.value = []
      })
    return listPromise
  }

  async function getSvg(name) {
    if (name in svgCache) return svgCache[name]
    await loadList()
    return svgCache[name] ?? ''
  }

  function isSvgIcon(value) {
    return typeof value === 'string' && value.startsWith('svg:')
  }

  function svgIconName(value) {
    return value.slice(4)
  }

  function invalidateCache() {
    listPromise = null
    iconNames.value = []
    Object.keys(svgCache).forEach(k => delete svgCache[k])
  }

  return { iconNames, loadList, getSvg, isSvgIcon, svgIconName, invalidateCache }
}
