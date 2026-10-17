import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SafeMarkdown from '@/components/ui/SafeMarkdown.vue'

function render(source) {
  return mount(SafeMarkdown, { props: { source } })
}

describe('SafeMarkdown', () => {
  it('renders headings, lists, code and inline markup', () => {
    const w = render([
      '# Top',
      '### Fixes',
      '##### Small',
      '* **Bold** item with `code`',
      '1. numbered [link](https://example.org/x)',
      '',
      'Text',
      '```',
      'raw <b>code</b>',
      '```',
    ].join('\n'))
    expect(w.find('h3').text()).toBe('Top')
    expect(w.find('h5').text()).toBe('Fixes')
    expect(w.find('h6').text()).toBe('Small')
    expect(w.find('ul li strong').text()).toBe('Bold')
    expect(w.find('ul li code').text()).toBe('code')
    const link = w.find('ol li a')
    expect(link.attributes()).toMatchObject({ href: 'https://example.org/x', target: '_blank', rel: 'noopener noreferrer nofollow' })
    expect(w.find('p').text()).toBe('Text')
    expect(w.find('pre').text()).toBe('raw <b>code</b>')
    expect(w.find('pre b').exists()).toBe(false)
  })

  it('never turns embedded HTML into elements', () => {
    const w = render('<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n[x](javascript:alert(1))')
    expect(w.find('script').exists()).toBe(false)
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('a').exists()).toBe(false)
    expect(w.text()).toContain('<script>alert(1)</script>')
  })

  it('renders nothing for an empty source', () => {
    expect(render('').element.children).toHaveLength(0)
    expect(mount(SafeMarkdown).text()).toBe('')
  })
})
