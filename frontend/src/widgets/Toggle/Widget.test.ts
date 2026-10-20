// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ToggleWidget from './Widget.vue'

const getSvg = vi.fn<(name: string) => Promise<string>>()

vi.mock('@/api/client', () => ({
  datapoints: { write: vi.fn().mockResolvedValue(undefined) },
}))

vi.mock('@/composables/useIcons', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/composables/useIcons')>()),
  useIcons: () => ({
    getSvg,
    isSvgIcon: (icon: string) => icon.startsWith('svg:'),
    svgIconName: (icon: string) => icon.slice(4),
  }),
}))

const PNG_URI = 'data:image/png;base64,iVBORw0KGgo='
const SVG_NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'

let wrapper: VueWrapper | null = null

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  getSvg.mockReset()
})

async function renderIcon(svg: string): Promise<Element> {
  getSvg.mockResolvedValue(svg)
  wrapper = mount(ToggleWidget, {
    props: {
      config: { mode: 'icon_only', on: { icon: 'svg:car', color: '#ff0000', text: 'EIN' } },
      datapointId: 'dp-1',
      value: { id: 'dp-1', v: true, u: null, t: '2026-10-20T00:00:00Z', q: 'good' },
      statusValue: null,
      editorMode: false,
    },
  })
  await flushPromises()
  const icon = wrapper.get('[data-testid="toggle-icon"]').element.querySelector('svg')
  expect(icon).not.toBeNull()
  return icon as Element
}

describe('Toggle Widget.vue — SVG icon sanitizing', () => {
  it('keeps an imported raster icon (PNG/JPEG wrapped in an SVG)', async () => {
    const icon = await renderIcon(`<svg ${SVG_NS} viewBox="0 0 2 1"><image width="2" height="1" href="${PNG_URI}"/></svg>`)
    expect(icon.querySelector('image')?.getAttribute('href')).toBe(PNG_URI)
  })

  it('keeps a raster image referenced through xlink:href, but drops its event handlers', async () => {
    const icon = await renderIcon(`<svg ${SVG_NS}><image xlink:href="${PNG_URI}" onload="alert(1)"/></svg>`)
    const image = icon.querySelector('image')
    expect(image?.getAttribute('xlink:href')).toBe(PNG_URI)
    expect(image?.hasAttribute('onload')).toBe(false)
  })

  it.each([
    ['an SVG data URI', '<image href="data:image/svg+xml;base64,PHN2Zy8+"/>'],
    ['an external URL', '<image href="https://example.com/a.png"/>'],
    ['a raster and a script URL', `<image href="${PNG_URI}" xlink:href="javascript:alert(1)"/>`],
    ['no href at all', '<image width="2" height="1"/>'],
  ])('removes an <image> with %s', async (_label, image) => {
    const icon = await renderIcon(`<svg ${SVG_NS}><path d="M0 0"/>${image}</svg>`)
    expect(icon.querySelector('image')).toBeNull()
    expect(icon.querySelector('path')).not.toBeNull()
  })

  it('still strips a raster data URI from elements other than <image>', async () => {
    const icon = await renderIcon(`<svg ${SVG_NS}><a href="${PNG_URI}"><path d="M0 0"/></a></svg>`)
    expect(icon.querySelector('a')?.hasAttribute('href')).toBe(false)
  })
})
