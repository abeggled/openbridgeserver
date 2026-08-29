// Regression tests for generate-help-index.mjs.
// Run via `node --test scripts/` (Node's built-in test runner — no extra
// devDependency needed, matching this package's otherwise-empty test setup).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs'
import { join, sep } from 'node:path'
import { tmpdir } from 'node:os'

import { localeAndRoutePath, routePartsToUrl, buildHelpIndex, generate } from './generate-help-index.mjs'

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
