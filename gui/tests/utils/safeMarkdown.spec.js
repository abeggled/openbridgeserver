import { describe, it, expect } from 'vitest'
import { parseInline, parseMarkdown, safeHref } from '@/utils/safeMarkdown'

describe('safeHref', () => {
  it('keeps absolute http(s) URLs', () => {
    expect(safeHref('https://github.com/o/r/issues/1')).toBe('https://github.com/o/r/issues/1')
    expect(safeHref(' http://example.org ')).toBe('http://example.org/')
  })

  it.each([
    ['javascript:alert(1)'],
    ['JAVASCRIPT:alert(1)'],
    ['data:text/html,<script>alert(1)</script>'],
    ['/relative/path'],
    ['//evil.example'],
    ['https://'],
    [''],
    [null],
    [42],
  ])('rejects %s', (url) => {
    expect(safeHref(url)).toBeNull()
  })
})

describe('parseInline', () => {
  it('splits code, bold, links and bare URLs', () => {
    expect(parseInline('Use `obs-update` and **read** [the docs](https://x.example/d) or https://y.example/p.')).toEqual([
      { type: 'text', text: 'Use ' },
      { type: 'code', text: 'obs-update' },
      { type: 'text', text: ' and ' },
      { type: 'strong', text: 'read' },
      { type: 'text', text: ' ' },
      { type: 'link', text: 'the docs', href: 'https://x.example/d' },
      { type: 'text', text: ' or ' },
      { type: 'link', text: 'https://y.example/p', href: 'https://y.example/p' },
      { type: 'text', text: '.' },
    ])
  })

  it('keeps balanced parentheses inside link URLs', () => {
    expect(parseInline('[docs](https://en.wikipedia.org/wiki/Foo_(bar)) end')).toEqual([
      { type: 'link', text: 'docs', href: 'https://en.wikipedia.org/wiki/Foo_(bar)' },
      { type: 'text', text: ' end' },
    ])
  })

  it('stays fast on long paragraphs with unmatched brackets', () => {
    // Unbounded, every '[' scanned to the end of the string: ~10^10 steps here.
    const text = '[x '.repeat(100000) + '`' + '*'
    const started = performance.now()
    const nodes = parseInline(text)
    expect(performance.now() - started).toBeLessThan(2000)
    expect(nodes).toEqual([{ type: 'text', text }])
  })

  it('can be called repeatedly (regex state is reset)', () => {
    expect(parseInline('`a` b')).toEqual(parseInline('`a` b'))
  })

  it('supports __bold__', () => {
    expect(parseInline('__x__')).toEqual([{ type: 'strong', text: 'x' }])
  })

  it('never emphasises __ inside a word, like GitHub', () => {
    for (const text of ['snake__case__name', 'a__b__', '__b__c', 'größe__x__']) {
      expect(parseInline(text)).toEqual([{ type: 'text', text }])
    }
    // Between punctuation it is emphasis on GitHub too: obs/__init__.py.
    expect(parseInline('obs/__init__.py')).toEqual([
      { type: 'text', text: 'obs/' },
      { type: 'strong', text: 'init' },
      { type: 'text', text: '.py' },
    ])
  })

  it('drops unsafe link targets but keeps the label', () => {
    expect(parseInline('[click](javascript:alert(1))')).toEqual([{ type: 'text', text: 'click' }])
    expect(parseInline('[x](data:text/html,1)')).toEqual([{ type: 'text', text: 'x' }])
  })

  it('keeps an unparsable bare URL as text', () => {
    expect(parseInline('https://[x]y')).toEqual([{ type: 'text', text: 'https://[x]y' }])
  })

  it('returns plain text when nothing matches', () => {
    expect(parseInline('<img src=x onerror=alert(1)>')).toEqual([{ type: 'text', text: '<img src=x onerror=alert(1)>' }])
  })
})

describe('parseMarkdown', () => {
  it('returns nothing for empty or non-string input', () => {
    expect(parseMarkdown('')).toEqual([])
    expect(parseMarkdown(null)).toEqual([])
  })

  it('parses the release notes structure', () => {
    const md = [
      '### New features ✨',
      '* Adapters: adds **WEBHOOK**',
      '  continued line',
      '- second item',
      '',
      '1. first',
      '2) second',
      '',
      'A paragraph',
      'over two lines.',
      '```bash',
      'docker pull ghcr.io/x:2026.10.0',
      '```',
      '<!-- LXC_INSERT -->',
      '## Heading with closing hashes ##',
    ].join('\r\n')
    expect(parseMarkdown(md)).toEqual([
      { type: 'heading', level: 3, children: [{ type: 'text', text: 'New features ✨' }] },
      {
        type: 'list',
        ordered: false,
        items: [
          [
            { type: 'text', text: 'Adapters: adds ' },
            { type: 'strong', text: 'WEBHOOK' },
            { type: 'text', text: ' ' },
            { type: 'text', text: 'continued line' },
          ],
          [{ type: 'text', text: 'second item' }],
        ],
      },
      { type: 'list', ordered: true, items: [[{ type: 'text', text: 'first' }], [{ type: 'text', text: 'second' }]] },
      { type: 'paragraph', children: [{ type: 'text', text: 'A paragraph over two lines.' }] },
      { type: 'code', text: 'docker pull ghcr.io/x:2026.10.0' },
      { type: 'heading', level: 2, children: [{ type: 'text', text: 'Heading with closing hashes' }] },
    ])
  })

  it('keeps a # that belongs to the heading text', () => {
    expect(parseMarkdown('## Support for C#')[0].children).toEqual([{ type: 'text', text: 'Support for C#' }])
    expect(parseMarkdown('## Closed ##')[0].children).toEqual([{ type: 'text', text: 'Closed' }])
    expect(parseMarkdown('## Plain   ')[0].children).toEqual([{ type: 'text', text: 'Plain' }])
    // Only a closing sequence: an empty heading (CommonMark).
    expect(parseMarkdown('### ###')[0]).toEqual({ type: 'heading', level: 3, children: [] })
  })

  it('parses a heading with a long whitespace run in linear time', () => {
    // The previous pattern backtracked quadratically: about 1 s for 40,000 spaces.
    const line = `# a${' '.repeat(200_000)}b`
    const started = performance.now()
    const [heading] = parseMarkdown(line)
    expect(performance.now() - started).toBeLessThan(500)
    expect(heading.children.at(-1).text.endsWith('b')).toBe(true)
  })

  it('switches between bullet and numbered lists', () => {
    const blocks = parseMarkdown('- a\n1. b\n- c')
    expect(blocks.map((b) => b.ordered)).toEqual([false, true, false])
  })

  it('closes a list and a paragraph before a fence or heading', () => {
    expect(parseMarkdown('- a\n~~~\ncode\n~~~').map((b) => b.type)).toEqual(['list', 'code'])
    expect(parseMarkdown('text\n# H').map((b) => b.type)).toEqual(['paragraph', 'heading'])
  })

  it('keeps an unterminated fence and comment harmless', () => {
    expect(parseMarkdown('```\n<script>alert(1)</script>')).toEqual([{ type: 'code', text: '<script>alert(1)</script>' }])
    expect(parseMarkdown('before\n<!-- open comment\n<script>x</script>')).toEqual([
      { type: 'paragraph', children: [{ type: 'text', text: 'before' }] },
    ])
  })

  it('keeps embedded HTML as literal text', () => {
    expect(parseMarkdown('<script>alert(1)</script>')).toEqual([
      { type: 'paragraph', children: [{ type: 'text', text: '<script>alert(1)</script>' }] },
    ])
  })
})
