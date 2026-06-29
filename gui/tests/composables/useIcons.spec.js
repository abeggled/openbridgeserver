import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

let iconsApiMock

beforeEach(() => {
  vi.resetModules()
  iconsApiMock = { list: vi.fn() }
  vi.doMock('@/api/client', () => ({ iconsApi: iconsApiMock }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

describe('useIcons — isSvgIcon / svgIconName', () => {
  it('isSvgIcon returns true for svg: prefixed strings', async () => {
    const { useIcons } = await import('@/composables/useIcons')
    const { isSvgIcon } = useIcons()
    expect(isSvgIcon('svg:my-icon')).toBe(true)
    expect(isSvgIcon('mdi:home')).toBe(false)
    expect(isSvgIcon('')).toBe(false)
    expect(isSvgIcon(null)).toBe(false)
  })

  it('svgIconName strips the svg: prefix', async () => {
    const { useIcons } = await import('@/composables/useIcons')
    const { svgIconName } = useIcons()
    expect(svgIconName('svg:my-icon')).toBe('my-icon')
    expect(svgIconName('svg:obs-logo')).toBe('obs-logo')
  })
})

describe('useIcons — loadList', () => {
  it('fetches icons and populates iconNames and svgCache', async () => {
    iconsApiMock.list.mockResolvedValue({
      data: {
        icons: [
          { name: 'lamp', content: '<svg width="24" height="24"><path/></svg>' },
          { name: 'fan',  content: '<svg><circle/></svg>' },
        ],
      },
    })
    const { useIcons } = await import('@/composables/useIcons')
    const { loadList, iconNames, getSvg } = useIcons()

    await loadList()

    expect(iconNames.value).toEqual(['lamp', 'fan'])
    const svg = await getSvg('lamp')
    // normalizeSvg strips width/height attributes
    expect(svg).not.toContain('width="24"')
    expect(svg).not.toContain('height="24"')
    expect(svg).toContain('<svg')
  })

  it('deduplicates concurrent loadList calls (only one API request)', async () => {
    iconsApiMock.list.mockResolvedValue({ data: { icons: [] } })
    const { useIcons } = await import('@/composables/useIcons')
    const { loadList } = useIcons()

    await Promise.all([loadList(), loadList(), loadList()])

    expect(iconsApiMock.list).toHaveBeenCalledTimes(1)
  })

  it('handles empty icons array gracefully', async () => {
    iconsApiMock.list.mockResolvedValue({ data: {} })
    const { useIcons } = await import('@/composables/useIcons')
    const { loadList, iconNames } = useIcons()

    await loadList()

    expect(iconNames.value).toEqual([])
  })

  it('resets listPromise on fetch error so next call retries', async () => {
    iconsApiMock.list
      .mockRejectedValueOnce(new Error('network error'))
      .mockResolvedValueOnce({ data: { icons: [{ name: 'ok', content: '<svg/>' }] } })
    const { useIcons } = await import('@/composables/useIcons')
    const { loadList, iconNames } = useIcons()

    await loadList()
    expect(iconNames.value).toEqual([])

    // Second call must retry (listPromise was reset)
    await loadList()
    expect(iconNames.value).toEqual(['ok'])
    expect(iconsApiMock.list).toHaveBeenCalledTimes(2)
  })
})

describe('useIcons — getSvg', () => {
  it('returns cached svg without re-fetching', async () => {
    iconsApiMock.list.mockResolvedValue({
      data: { icons: [{ name: 'lamp', content: '<svg/>' }] },
    })
    const { useIcons } = await import('@/composables/useIcons')
    const { loadList, getSvg } = useIcons()

    await loadList()
    await getSvg('lamp')
    await getSvg('lamp')

    expect(iconsApiMock.list).toHaveBeenCalledTimes(1)
  })

  it('returns empty string for unknown icon name', async () => {
    iconsApiMock.list.mockResolvedValue({ data: { icons: [] } })
    const { useIcons } = await import('@/composables/useIcons')
    const { getSvg } = useIcons()

    const result = await getSvg('nonexistent')
    expect(result).toBe('')
  })
})

describe('useIcons — invalidateCache', () => {
  it('clears iconNames, svgCache and listPromise so next loadList re-fetches', async () => {
    iconsApiMock.list.mockResolvedValue({
      data: { icons: [{ name: 'lamp', content: '<svg/>' }] },
    })
    const { useIcons } = await import('@/composables/useIcons')
    const { loadList, iconNames, invalidateCache } = useIcons()

    await loadList()
    expect(iconNames.value).toEqual(['lamp'])

    invalidateCache()
    expect(iconNames.value).toEqual([])

    // loadList should re-fetch after invalidation
    iconsApiMock.list.mockResolvedValue({ data: { icons: [] } })
    await loadList()
    expect(iconsApiMock.list).toHaveBeenCalledTimes(2)
  })
})
