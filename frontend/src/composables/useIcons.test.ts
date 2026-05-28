// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'

async function loadUseIconsWithSvg(svgContent: string) {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    icons: {
      list: vi.fn().mockResolvedValue({
        icons: [{ name: 'test-icon', content: svgContent }],
      }),
    },
  }))
  const mod = await import('./useIcons')
  return mod.useIcons()
}

describe('useIcons sanitizeSvg', () => {
  it('blocks javascript href with embedded control chars', async () => {
    const { getSvg } = await loadUseIconsWithSvg(
      '<svg xmlns="http://www.w3.org/2000/svg"><a href="java&#x0A;script:alert(1)"><rect width="12" height="8"/></a></svg>',
    )

    const sanitized = await getSvg('test-icon')
    expect(sanitized).not.toContain('href=')
  })

  it('still removes root width/height attributes', async () => {
    const { getSvg } = await loadUseIconsWithSvg(
      '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="12" height="8"/></svg>',
    )

    const sanitized = await getSvg('test-icon')
    expect(sanitized).not.toContain(' width="100"')
    expect(sanitized).not.toContain(' height="50"')
  })

  it('removes SMIL animation elements that can mutate links', async () => {
    const { getSvg } = await loadUseIconsWithSvg(
      '<svg xmlns="http://www.w3.org/2000/svg"><a href="#"><animate attributeName="href" values="javascript:alert(1)"/></a></svg>',
    )

    const sanitized = await getSvg('test-icon')
    expect(sanitized).not.toContain('<animate')
  })
})
