// Regression tests for generate-help-index.mjs.
// Run via `node --test scripts/` (Node's built-in test runner — no extra
// devDependency needed, matching this package's otherwise-empty test setup).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs'
import { join, sep } from 'node:path'
import { tmpdir } from 'node:os'

import { localeAndRoutePath, routePartsToUrl, buildHelpIndex, generate, stripFencedCode, stripHtmlComments, stripRawHtmlBlocks, stripFrontmatter } from './generate-help-index.mjs'

// ── Pure URL-mapping helpers ────────────────────────────────────────────────

test('de locale URLs keep the /de/ prefix, matching every other locale', () => {
  const relPath = ['de', 'settings', 'general.md'].join(sep)
  const { locale, routeParts } = localeAndRoutePath(relPath)
  assert.equal(locale, 'de')
  assert.equal(routePartsToUrl(routeParts), '/help/de/settings/general.html')
})

test('en locale URLs keep the /en/ prefix, distinct from the de URL', () => {
  const relPath = ['en', 'settings', 'general.md'].join(sep)
  const { locale, routeParts } = localeAndRoutePath(relPath)
  assert.equal(locale, 'en')
  const url = routePartsToUrl(routeParts)
  assert.equal(url, '/help/en/settings/general.html')

  const deUrl = routePartsToUrl(localeAndRoutePath(['de', 'settings', 'general.md'].join(sep)).routeParts)
  assert.notEqual(url, deUrl, 'en and de must resolve to different URLs, or English readers get German content')
})

test('a file outside every recognized locale directory is rejected, not silently treated as German', () => {
  assert.throws(() => localeAndRoutePath('settings/general.md'), /not under a recognized locale directory/)
  assert.throws(() => localeAndRoutePath('index.md'), /not under a recognized locale directory/)
})

test('index.md maps to its locale root', () => {
  assert.equal(routePartsToUrl(localeAndRoutePath(['de', 'index.md'].join(sep)).routeParts), '/help/de/')
  assert.equal(routePartsToUrl(localeAndRoutePath(['en', 'index.md'].join(sep)).routeParts), '/help/en/')
})

// ── buildHelpIndex() / generate() — realistic fixture tree ─────────────────

function withFixture(files, fn) {
  const root = mkdtempSync(join(tmpdir(), 'help-index-test-'))
  try {
    for (const [relPath, content] of Object.entries(files)) {
      const abs = join(root, relPath)
      mkdirSync(join(abs, '..'), { recursive: true })
      writeFileSync(abs, content)
    }
    return fn(root)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}

test('buildHelpIndex indexes a single explicit-id heading', () => {
  withFixture(
    {
      'de/settings/general.md': '# Allgemein {#settings-general}\n\nText.\n',
    },
    (root) => {
      const { helpIds, duplicates, incomplete } = buildHelpIndex(root)
      assert.deepEqual(duplicates, [])
      assert.equal(helpIds['settings-general'].de, '/help/de/settings/general.html#settings-general')
      // Only the 'de' locale exists in this fixture — 'en' must be reported missing.
      assert.deepEqual(incomplete, [{ id: 'settings-general', missing: ['en'] }])
    }
  )
})

test('buildHelpIndex extracts multiple anchored headings from one file', () => {
  withFixture(
    {
      'de/settings/general.md': [
        '# Allgemein {#settings-general}',
        '',
        '## Sprache {#settings-general-language}',
        '',
        '## Aussehen {#settings-general-appearance}',
        '',
      ].join('\n'),
    },
    (root) => {
      const { helpIds } = buildHelpIndex(root)
      assert.deepEqual(
        Object.keys(helpIds).sort(),
        ['settings-general', 'settings-general-appearance', 'settings-general-language']
      )
    }
  )
})

test('buildHelpIndex ignores headings without an explicit {#id} anchor', () => {
  withFixture(
    {
      'de/settings/general.md': '# Allgemein\n\n## Ohne Anker\n\nText ohne help_id.\n',
    },
    (root) => {
      const { helpIds } = buildHelpIndex(root)
      assert.deepEqual(helpIds, {})
    }
  )
})

test('buildHelpIndex ignores non-.md files even if they contain anchor-like text', () => {
  withFixture(
    {
      'de/settings/general.md': '# Allgemein {#settings-general}\n',
      'de/settings/notes.json': '{"heading": "# Fake {#not-real}"}',
    },
    (root) => {
      const { helpIds } = buildHelpIndex(root)
      assert.deepEqual(Object.keys(helpIds), ['settings-general'])
    }
  )
})

test('buildHelpIndex excludes .vitepress/public/node_modules/scripts only at the top level', () => {
  withFixture(
    {
      '.vitepress/config.mts': '// not markdown, but even a stray .md here must not be scanned',
      'public/stray.md': '# Should be skipped {#public-stray}',
      'node_modules/pkg/readme.md': '# Should be skipped {#node-modules-stray}',
      'scripts/notes.md': '# Should be skipped {#scripts-stray}',
      // A directory that happens to share a name with an excluded one, but
      // nested (not at the scanned root), must NOT be excluded.
      'de/settings/scripts/tips.md': '# Nested scripts dir is fine {#settings-scripts-tips}',
    },
    (root) => {
      const { helpIds } = buildHelpIndex(root)
      assert.deepEqual(Object.keys(helpIds), ['settings-scripts-tips'])
    }
  )
})

// ── Fenced code is documentation, not an anchor ────────────────────────────

test('an anchor-shaped heading inside a fenced block is not indexed', () => {
  // VitePress renders it as <code> and creates no DOM id, so a help_id taken
  // from here would resolve to a fragment no element answers to.
  const root = mkdtempSync(join(tmpdir(), 'help-fence-'))
  try {
    for (const locale of ['de', 'en']) {
      mkdirSync(join(root, locale), { recursive: true })
      writeFileSync(
        join(root, locale, 'index.md'),
        ['# Title {#real-anchor}', '', 'Write an anchor like this:', '', '```md', '## Example {#only-in-code}', '```', ''].join('\n')
      )
    }

    const { helpIds } = buildHelpIndex(root)

    assert.ok('real-anchor' in helpIds)
    assert.ok(!('only-in-code' in helpIds), 'an example inside a fence must not become a help_id')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('stripFencedCode blanks fenced blocks and keeps every line position', () => {
  const text = ['# A {#a}', '```', '## B {#b}', '```', '## C {#c}'].join('\n')

  const stripped = stripFencedCode(text)

  assert.equal(stripped.split('\n').length, 5)
  assert.match(stripped, /# A \{#a\}/)
  assert.match(stripped, /## C \{#c\}/)
  assert.doesNotMatch(stripped, /\{#b\}/)
})

test('stripFencedCode handles tilde fences and a longer closing marker', () => {
  const text = ['~~~js', '## B {#b}', '~~~', '````', '## D {#d}', '`````', '## E {#e}'].join('\n')

  const stripped = stripFencedCode(text)

  assert.doesNotMatch(stripped, /\{#b\}/)
  assert.doesNotMatch(stripped, /\{#d\}/)
  assert.match(stripped, /## E \{#e\}/)
})

test('stripFencedCode does not treat a shorter inner marker as the closing fence', () => {
  const text = ['````', '```', '## B {#b}', '````', '## C {#c}'].join('\n')

  const stripped = stripFencedCode(text)

  assert.doesNotMatch(stripped, /\{#b\}/)
  assert.match(stripped, /## C \{#c\}/)
})

test('stripFencedCode leaves an inline-code line alone', () => {
  // ```js`` is inline code, not a fence: its info string contains a backtick.
  const text = ['# A {#a}', '``` `not a fence` ```', '## B {#b}'].join('\n')

  const stripped = stripFencedCode(text)

  assert.match(stripped, /## B \{#b\}/)
})

test('an anchor-shaped heading inside an HTML comment is not indexed', () => {
  // markdown-it drops the comment entirely, so no element owns that id.
  const root = mkdtempSync(join(tmpdir(), 'help-comment-'))
  try {
    for (const locale of ['de', 'en']) {
      mkdirSync(join(root, locale), { recursive: true })
      writeFileSync(
        join(root, locale, 'index.md'),
        ['# Title {#real-anchor}', '', '<!--', '## Internal note {#only-in-comment}', '-->', ''].join('\n')
      )
    }

    const { helpIds } = buildHelpIndex(root)

    assert.ok('real-anchor' in helpIds)
    assert.ok(!('only-in-comment' in helpIds), 'an anchor inside an HTML comment must not become a help_id')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('stripHtmlComments blanks single- and multi-line comments and keeps line positions', () => {
  const text = ['# A {#a}', '<!-- ## B {#b} -->', '<!--', '## C {#c}', '-->', '## D {#d}'].join('\n')

  const stripped = stripHtmlComments(text)

  assert.equal(stripped.split('\n').length, 6)
  assert.doesNotMatch(stripped, /\{#b\}/)
  assert.doesNotMatch(stripped, /\{#c\}/)
  assert.match(stripped, /## D \{#d\}/)
})

test('a --> inside a fenced block does not end a comment that never started', () => {
  const text = ['```html', '<!-- example -->', '```', '## A {#a}'].join('\n')

  const stripped = stripHtmlComments(stripFencedCode(text))

  assert.match(stripped, /## A \{#a\}/)
})

test('an anchor inside a raw HTML block is not indexed, but a blank-line-separated one is', () => {
  // Verified against a real VitePress build: `<div>\n## X {#id}\n</div>`
  // renders no id, while the same heading surrounded by blank lines does.
  const root = mkdtempSync(join(tmpdir(), 'help-rawhtml-'))
  try {
    for (const locale of ['de', 'en']) {
      mkdirSync(join(root, locale), { recursive: true })
      writeFileSync(
        join(root, locale, 'index.md'),
        ['# T {#real-anchor}', '', '<div class="tip">', '## Inside {#only-in-html}', '</div>', '', '<div>', '', '## Separated {#separated}', '', '</div>', ''].join('\n')
      )
    }

    const { helpIds } = buildHelpIndex(root)

    assert.ok('real-anchor' in helpIds)
    assert.ok('separated' in helpIds, 'a heading separated by blank lines is rendered and must stay indexed')
    assert.ok(!('only-in-html' in helpIds), 'a heading inside a raw HTML block owns no DOM id')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('a type-1 HTML block stays open across blank lines until its closing tag', () => {
  // Verified against a real build: a heading inside <pre> renders no id even
  // with blank lines around it, unlike a <div> block which a blank line ends.
  const stripped = stripRawHtmlBlocks(['<pre>', 'text', '', '## Inside {#gone}', '', '</pre>', '', '## After {#kept}'].join('\n'))

  assert.doesNotMatch(stripped, /\{#gone\}/)
  assert.match(stripped, /## After \{#kept\}/)
})

test('a blank line still ends an ordinary HTML block', () => {
  const stripped = stripRawHtmlBlocks(['<div>', '## InDiv {#gone}', '', '## After {#kept}'].join('\n'))

  assert.doesNotMatch(stripped, /\{#gone\}/)
  assert.match(stripped, /## After \{#kept\}/)
})

test('stripRawHtmlBlocks leaves an autolink paragraph alone', () => {
  // `<https://example.com>` is not a tag, so it opens no HTML block.
  const stripped = stripRawHtmlBlocks(['<https://example.com>', '## E {#e}'].join('\n'))

  assert.match(stripped, /## E \{#e\}/)
})

test('stripRawHtmlBlocks resumes indexing after the block ends', () => {
  const stripped = stripRawHtmlBlocks(['<div>', '## B {#b}', '</div>', '', '## C {#c}'].join('\n'))

  assert.doesNotMatch(stripped, /\{#b\}/)
  assert.match(stripped, /## C \{#c\}/)
})

test('a heading-shaped line in the frontmatter is not indexed, an indented heading is', () => {
  // Both verified against a real build: the frontmatter block is removed from
  // the page, while CommonMark allows up to three spaces before an ATX heading.
  const root = mkdtempSync(join(tmpdir(), 'help-front-'))
  try {
    for (const locale of ['de', 'en']) {
      mkdirSync(join(root, locale), { recursive: true })
      writeFileSync(
        join(root, locale, 'index.md'),
        ['---', '# ## Frontmatter {#gone}', 'title: T', '---', '', '   ### Indented {#kept}', ''].join('\n')
      )
    }

    const { helpIds } = buildHelpIndex(root)

    assert.ok('kept' in helpIds, 'up to three spaces before a heading is valid CommonMark')
    assert.ok(!('gone' in helpIds), 'frontmatter is removed from the rendered page')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('stripFrontmatter only removes a leading block and keeps line positions', () => {
  const text = ['---', 'title: T', '---', '## A {#a}'].join('\n')

  const stripped = stripFrontmatter(text)

  assert.equal(stripped.split('\n').length, 4)
  assert.match(stripped, /## A \{#a\}/)
  assert.doesNotMatch(stripped, /title/)
  // A `---` later in the page is a thematic break, not frontmatter.
  assert.match(stripFrontmatter('## A {#a}\n\n---\n\n## B {#b}'), /## A \{#a\}/)
})

test('a closing hash sequence does not hide the anchor', () => {
  const root = mkdtempSync(join(tmpdir(), 'help-closing-'))
  try {
    for (const locale of ['de', 'en']) {
      mkdirSync(join(root, locale), { recursive: true })
      writeFileSync(join(root, locale, 'index.md'), ['## Closing {#closing} ##', '', '## Plain {#plain}', ''].join('\n'))
    }

    assert.deepEqual(Object.keys(buildHelpIndex(root).helpIds).sort(), ['closing', 'plain'])
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('an escaped anchor is not indexed', () => {
  // Verified against a real build: `## Title \\{#probe}` renders the suffix as
  // visible text and gets markdown-it's auto-slug of the whole heading, so the
  // requested fragment does not exist.
  const root = mkdtempSync(join(tmpdir(), 'help-escaped-'))
  try {
    for (const locale of ['de', 'en']) {
      mkdirSync(join(root, locale), { recursive: true })
      writeFileSync(join(root, locale, 'index.md'), ['## Escaped \\{#gone}', '', '## Real {#kept}', ''].join('\n'))
    }

    const { helpIds } = buildHelpIndex(root)

    assert.ok('kept' in helpIds)
    assert.ok(!('gone' in helpIds), 'an escaped anchor renders as text and owns no id')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('the same help_id in two different locales is not a duplicate and is reported complete', () => {
  withFixture(
    {
      'de/settings/general.md': '# Allgemein {#settings-general}',
      'en/settings/general.md': '# General {#settings-general}',
    },
    (root) => {
      const { helpIds, duplicates, incomplete } = buildHelpIndex(root)
      assert.deepEqual(duplicates, [])
      assert.deepEqual(incomplete, [])
      assert.equal(helpIds['settings-general'].de, '/help/de/settings/general.html#settings-general')
      assert.equal(helpIds['settings-general'].en, '/help/en/settings/general.html#settings-general')
    }
  )
})

test('the same help_id reused twice within one locale is flagged as a duplicate, first occurrence wins', () => {
  withFixture(
    {
      'de/settings/general.md': '# Allgemein {#settings-general}',
      'de/settings/password.md': '# Passwort {#settings-general}',
    },
    (root) => {
      const { helpIds, duplicates } = buildHelpIndex(root)
      assert.equal(duplicates.length, 1)
      assert.match(duplicates[0], /duplicate help_id "settings-general" in locale "de"/)
      // Files are processed in sorted order — general.md sorts before password.md.
      assert.equal(helpIds['settings-general'].de, '/help/de/settings/general.html#settings-general')
    }
  )
})

test('generate() writes help-index.json with the built index and a generatedAt timestamp', () => {
  withFixture(
    {
      'de/settings/general.md': '# Allgemein {#settings-general}',
      'en/settings/general.md': '# General {#settings-general}',
    },
    (root) => {
      const outDir = join(root, 'out')
      const outFile = generate(root, outDir)
      const written = JSON.parse(readFileSync(outFile, 'utf-8'))
      assert.ok(written.generatedAt)
      assert.equal(written.helpIds['settings-general'].de, '/help/de/settings/general.html#settings-general')
      assert.equal(written.helpIds['settings-general'].en, '/help/en/settings/general.html#settings-general')
    }
  )
})

test('generate() still writes the file and warns (non-blocking) when a locale translation is missing', () => {
  withFixture(
    {
      'de/settings/general.md': '# Allgemein {#settings-general}',
      // No en/settings/general.md — the id is incomplete, not a duplicate.
    },
    (root) => {
      const outDir = join(root, 'out')
      const originalWarn = console.warn
      const warnings = []
      console.warn = (...args) => warnings.push(args.join(' '))
      try {
        const outFile = generate(root, outDir)
        const written = JSON.parse(readFileSync(outFile, 'utf-8'))
        assert.equal(written.helpIds['settings-general'].de, '/help/de/settings/general.html#settings-general')
        assert.ok(warnings.some((line) => line.includes('missing in at least one locale')))
        assert.ok(warnings.some((line) => line.includes('"settings-general" missing in: en')))
      } finally {
        console.warn = originalWarn
      }
    }
  )
})

test('generate() throws instead of calling process.exit() when duplicates exist', () => {
  withFixture(
    {
      'de/settings/general.md': '# Allgemein {#settings-general}',
      'de/settings/password.md': '# Passwort {#settings-general}',
    },
    (root) => {
      assert.throws(() => generate(root, join(root, 'out')), /duplicate help_id "settings-general"/)
    }
  )
})
