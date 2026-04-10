/**
 * Unit tests für die Icons-Logik aus SettingsView.vue
 *
 * Die reinen Logik-Funktionen (Filtering, Selektion, Dateinamen-Generierung)
 * werden hier isoliert ohne DOM oder Vue-Komponente getestet.
 */
import { describe, it, expect } from 'vitest'
import { ref, computed } from 'vue'

// ── Hilfsfunktionen (1:1 aus SettingsView.vue übernommen) ────────────────

/**
 * Filtert die Icon-Liste nach dem Suchbegriff (case-insensitiv, nach Name).
 */
function makeIconsFiltered(icons, iconsSearch) {
  return computed(() => {
    const q = iconsSearch.value.toLowerCase()
    if (!q) return icons.value
    return icons.value.filter(i => i.name.toLowerCase().includes(q))
  })
}

/**
 * Schaltet einen einzelnen Eintrag in der Selektions-Menge um.
 */
function iconsToggle(iconsSelected, name) {
  const sel = new Set(iconsSelected.value)
  if (sel.has(name)) sel.delete(name)
  else sel.add(name)
  iconsSelected.value = sel
}

/**
 * Wählt alle Icons aus oder hebt alle Auswahlen auf.
 */
function iconsSelectAll(iconsSelected, icons) {
  if (iconsSelected.value.size === icons.value.length) {
    iconsSelected.value = new Set()
  } else {
    iconsSelected.value = new Set(icons.value.map(i => i.name))
  }
}

/**
 * Erstellt den Zeitstempel-Teil des Export-Dateinamens.
 */
function _ts() {
  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}`
}

// ── Test-Daten ────────────────────────────────────────────────────────────

const SAMPLE_ICONS = [
  { name: 'home',          size: 512, content: '<svg/>' },
  { name: 'star',          size: 256, content: '<svg/>' },
  { name: 'circle-question', size: 384, content: '<svg/>' },
  { name: 'arrow-right',   size: 300, content: '<svg/>' },
]

// ── iconsFiltered ─────────────────────────────────────────────────────────

describe('iconsFiltered', () => {
  it('gibt alle Icons zurück wenn Suche leer ist', () => {
    const icons = ref(SAMPLE_ICONS)
    const search = ref('')
    const filtered = makeIconsFiltered(icons, search)
    expect(filtered.value).toHaveLength(4)
  })

  it('filtert nach exaktem Namen', () => {
    const icons = ref(SAMPLE_ICONS)
    const search = ref('home')
    const filtered = makeIconsFiltered(icons, search)
    expect(filtered.value).toHaveLength(1)
    expect(filtered.value[0].name).toBe('home')
  })

  it('filtert case-insensitiv', () => {
    const icons = ref(SAMPLE_ICONS)
    const search = ref('HOME')
    const filtered = makeIconsFiltered(icons, search)
    expect(filtered.value).toHaveLength(1)
    expect(filtered.value[0].name).toBe('home')
  })

  it('filtert nach Teilstring', () => {
    const icons = ref(SAMPLE_ICONS)
    const search = ref('arrow')
    const filtered = makeIconsFiltered(icons, search)
    expect(filtered.value).toHaveLength(1)
    expect(filtered.value[0].name).toBe('arrow-right')
  })

  it('filtert nach Teilstring der in mehreren Namen vorkommt', () => {
    const icons = ref(SAMPLE_ICONS)
    const search = ref('r')    // home, sta[r], ci[r]cle-question, a[r][r]ow-[r]ight
    const filtered = makeIconsFiltered(icons, search)
    const names = filtered.value.map(i => i.name)
    expect(names).toContain('star')
    expect(names).toContain('circle-question')
    expect(names).toContain('arrow-right')
    expect(names).not.toContain('home')
  })

  it('gibt leeres Array zurück wenn kein Treffer', () => {
    const icons = ref(SAMPLE_ICONS)
    const search = ref('xyz-nonexistent')
    const filtered = makeIconsFiltered(icons, search)
    expect(filtered.value).toHaveLength(0)
  })

  it('reagiert reaktiv auf Suchänderungen', () => {
    const icons = ref(SAMPLE_ICONS)
    const search = ref('')
    const filtered = makeIconsFiltered(icons, search)

    expect(filtered.value).toHaveLength(4)
    search.value = 'star'
    expect(filtered.value).toHaveLength(1)
    search.value = ''
    expect(filtered.value).toHaveLength(4)
  })
})

// ── iconsToggle ───────────────────────────────────────────────────────────

describe('iconsToggle', () => {
  it('fügt ein Icon zur Selektion hinzu', () => {
    const selected = ref(new Set())
    iconsToggle(selected, 'home')
    expect(selected.value.has('home')).toBe(true)
  })

  it('entfernt ein bereits selektiertes Icon', () => {
    const selected = ref(new Set(['home']))
    iconsToggle(selected, 'home')
    expect(selected.value.has('home')).toBe(false)
  })

  it('lässt andere Selektionen unberührt', () => {
    const selected = ref(new Set(['star']))
    iconsToggle(selected, 'home')
    expect(selected.value.has('star')).toBe(true)
    expect(selected.value.has('home')).toBe(true)
  })

  it('mehrfaches Togglen kehrt zurück zur ursprünglichen Selektion', () => {
    const selected = ref(new Set())
    iconsToggle(selected, 'home')
    iconsToggle(selected, 'home')
    expect(selected.value.has('home')).toBe(false)
    expect(selected.value.size).toBe(0)
  })
})

// ── iconsSelectAll ────────────────────────────────────────────────────────

describe('iconsSelectAll', () => {
  it('wählt alle Icons aus wenn keins ausgewählt ist', () => {
    const icons = ref(SAMPLE_ICONS)
    const selected = ref(new Set())
    iconsSelectAll(selected, icons)
    expect(selected.value.size).toBe(4)
    expect(selected.value.has('home')).toBe(true)
    expect(selected.value.has('star')).toBe(true)
  })

  it('wählt alle Icons aus wenn nur einige ausgewählt sind', () => {
    const icons = ref(SAMPLE_ICONS)
    const selected = ref(new Set(['home']))
    iconsSelectAll(selected, icons)
    expect(selected.value.size).toBe(4)
  })

  it('hebt alle Selektionen auf wenn alle ausgewählt sind', () => {
    const icons = ref(SAMPLE_ICONS)
    const selected = ref(new Set(SAMPLE_ICONS.map(i => i.name)))
    iconsSelectAll(selected, icons)
    expect(selected.value.size).toBe(0)
  })

  it('zweimaliges Aufrufen kehrt zur ursprünglichen leeren Selektion zurück', () => {
    const icons = ref(SAMPLE_ICONS)
    const selected = ref(new Set())
    iconsSelectAll(selected, icons)  // → alle
    iconsSelectAll(selected, icons)  // → keine
    expect(selected.value.size).toBe(0)
  })
})

// ── Export-Dateiname ──────────────────────────────────────────────────────

describe('_ts (Zeitstempel für Dateinamen)', () => {
  it('hat das Format YYYYMMDD_HHmm', () => {
    const ts = _ts()
    expect(ts).toMatch(/^\d{8}_\d{4}$/)
  })

  it('enthält das aktuelle Jahr', () => {
    const ts = _ts()
    expect(ts.startsWith(String(new Date().getFullYear()))).toBe(true)
  })
})
