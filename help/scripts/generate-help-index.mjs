#!/usr/bin/env node
// Generates help/public/help-index.json from explicit heading anchor IDs
// (`## Heading {#some-help-id}`) found in the Markdown sources under help/.
//
// VitePress passes markdown-it-anchor's explicit-ID syntax through natively —
// `{#some-help-id}` on a heading line becomes that heading's rendered `id`
// attribute — so a help_id IS the heading's HTML anchor id. This script does
// not invent a separate metadata layer; it just indexes those ids so the
// Admin-GUI can resolve a `help_id` prop to a locale-specific help URL
// without knowing VitePress's file layout.
//
// A `help_id` must be assigned deliberately (not derived from heading text)
// because heading text changes with wording fixes and differs per locale —
// an auto-slug would silently break any GUI component referencing it.
//
// Output is written to help/public/help-index.json, which VitePress's
// publicDir passthrough copies verbatim to help_dist/help-index.json — so it
// ships alongside the built site with no separate build step to wire up.
//
// Locale layout must match help/.vitepress/config.mts: every locale —
// including German — lives under its own prefixed directory (`de/`, `en/`,
// ...) and is served at that same prefixed URL; there is no unprefixed
// "root" locale. This mirrors gui/frontend's Weblate setup, where German is
// a normal (if usually already-complete) target language rather than the
// translation source — English is. When adding a new locale in config.mts,
// add its directory prefix to LOCALE_DIRS below too.

import { readFileSync, readdirSync, mkdirSync, writeFileSync } from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const HELP_ROOT = fileURLToPath(new URL('..', import.meta.url))
const LOCALE_DIRS = { de: 'de', en: 'en' } // dir prefix -> locale code — every locale is prefixed
const EXCLUDED_TOP_LEVEL = new Set(['.vitepress', 'public', 'node_modules', 'scripts'])

// The `{` must not be escaped: `## Title \{#id}` renders the suffix as visible
// heading text and gets markdown-it's auto-slug of that whole text instead
// (verified against a real build: `\{#probe}` produced id="title-probe", not
// "probe"), so indexing `id` would hand out a fragment nothing answers to.
// ATX (`## Title {#id}`) and Setext (`Title {#id}` over `===`/`---`) — both
// are headings VitePress renders with the explicit id, verified against a
// real build.
const HEADING_RE = /^ {0,3}#{1,6}\s+.*(?<!\\)\{#([A-Za-z][\w-]*)\}\s*$|^ {0,3}\S.*(?<!\\)\{#([A-Za-z][\w-]*)\}[^\S\r\n]*\r?\n {0,3}(?:=+|-+)[^\S\r\n]*$/gm

function findMarkdownFiles(dir, base = dir) {
  const entries = readdirSync(dir, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (dir === base && EXCLUDED_TOP_LEVEL.has(entry.name)) continue
      files.push(...findMarkdownFiles(full, base))
    } else if (entry.isFile() && entry.name.endsWith('.md')) {
      files.push(full)
    }
  }
  return files
}

export function localeAndRoutePath(relPath) {
  // Every locale is served at its own prefixed URL (e.g. `/help/de/...`,
  // `/help/en/...`) — routeParts keeps the full path, prefix included.
  const parts = relPath.split(sep)
  const [localeDir] = parts
  if (!(localeDir in LOCALE_DIRS)) {
    throw new Error(
      `generate-help-index: "${relPath}" is not under a recognized locale directory ` +
      `(${Object.keys(LOCALE_DIRS).join(', ')}) — move it under one of those, or add ` +
      `its directory to LOCALE_DIRS if this is a new locale.`
    )
  }
  return { locale: LOCALE_DIRS[localeDir], routeParts: parts }
}

export function routePartsToUrl(routeParts) {
  const withoutExt = routeParts.join('/').replace(/\.md$/, '')
  if (withoutExt === 'index' || withoutExt.endsWith('/index')) {
    const dir = withoutExt.slice(0, withoutExt.length - 'index'.length)
    return `/help/${dir}`
  }
  return `/help/${withoutExt}.html`
}

const FENCE_RE = /^ {0,3}(`{3,}|~{3,})(.*)$/

/**
 * Blank out fenced code blocks, keeping the line count so nothing downstream
 * shifts. An anchor-shaped heading inside a fence — a documentation example
 * showing how to write one — is rendered by VitePress as `<code>` text and
 * owns no DOM id, so indexing it would hand the Admin-GUI a help_id that
 * resolves to a fragment no element answers to.
 */
export function stripFencedCode(text) {
  let fence = null
  return text
    .split('\n')
    .map((line) => {
      const match = FENCE_RE.exec(line)
      if (match) {
        const marker = match[1]
        const char = marker[0]
        if (fence === null) {
          // A backtick fence's info string may not itself contain a backtick
          // (CommonMark) — that is inline code, not a fence.
          if (!(char === '`' && match[2].includes('`'))) {
            fence = { char, length: marker.length }
            return ''
          }
        } else if (char === fence.char && marker.length >= fence.length && match[2].trim() === '') {
          fence = null
          return ''
        }
      }
      return fence === null ? line : ''
    })
    .join('\n')
}

/**
 * Blank out HTML comments, keeping newlines so line positions hold. Same
 * reason as stripFencedCode: markdown-it drops a commented-out heading
 * entirely, so an anchor parked in one owns no DOM id and a help_id taken
 * from it would resolve to a fragment no element answers to. Applied after
 * the fences are blanked, so a stray `-->` inside a code block cannot end a
 * comment that never started.
 */
export function stripHtmlComments(text) {
  return text.replace(/<!--[\s\S]*?-->/g, (match) => match.replace(/[^\n]/g, ''))
}

// A tag name, not an autolink: `<https://example.com>` must stay a paragraph.
const HTML_BLOCK_START_RE = /^ {0,3}<\/?[A-Za-z][A-Za-z0-9-]*(?=[\s/>]|$)/
// CommonMark "type 1": these run to their closing tag rather than to the next
// blank line, so markdown inside them is never parsed.
const LITERAL_BLOCK_START_RE = /^ {0,3}<(pre|script|style|textarea)(?=[\s/>]|$)/i

/**
 * Blank out raw HTML blocks, keeping the line count. A heading-shaped line
 * directly inside one is not parsed as a heading — verified against a real
 * VitePress build: `<div>\n## X {#id}\n</div>` renders no `id`, while the
 * same heading separated by blank lines does. Indexing the former would hand
 * out a help_id no element answers to.
 *
 * The block ends at the first blank line, which is what lets the separated
 * form keep working.
 */
export function stripRawHtmlBlocks(text) {
  let inBlock = false
  let literalTag = null // a CommonMark "type 1" block, closed by its end tag
  return text
    .split('\n')
    .map((line) => {
      if (!inBlock) {
        const literal = LITERAL_BLOCK_START_RE.exec(line)
        if (literal) {
          inBlock = true
          literalTag = literal[1].toLowerCase()
        } else if (HTML_BLOCK_START_RE.test(line)) {
          inBlock = true
        }
      }
      if (!inBlock) return line
      if (literalTag !== null) {
        // Runs to its closing tag, blank lines included — verified against a
        // real build: a heading inside `<pre>` renders no id even with blank
        // lines around it.
        if (new RegExp(`</${literalTag}\\s*>`, 'i').test(line)) {
          inBlock = false
          literalTag = null
        }
        return ''
      }
      if (line.trim() === '') {
        inBlock = false
        return line
      }
      return ''
    })
    .join('\n')
}

const FRONTMATTER_RE = /^---\r?\n[\s\S]*?\r?\n---[^\S\r\n]*(\r?\n|$)/

/**
 * Blank a page's YAML frontmatter, keeping the line count. VitePress removes
 * the whole block from the rendered page, so a heading-shaped line inside it
 * (a YAML comment, say) owns no DOM id — indexing it would point a help
 * button at a fragment that does not exist.
 */
export function stripFrontmatter(text) {
  const match = FRONTMATTER_RE.exec(text)
  if (!match) return text
  return match[0].replace(/[^\n]/g, '') + text.slice(match[0].length)
}

function extractHelpIds(absPath) {
  const text = stripRawHtmlBlocks(stripHtmlComments(stripFencedCode(stripFrontmatter(readFileSync(absPath, 'utf-8')))))
  const ids = []
  for (const match of text.matchAll(HEADING_RE)) {
    ids.push(match[1] ?? match[2])
  }
  return ids
}

/**
 * Pure computation over `root`'s Markdown tree — no I/O beyond reading the
 * source files. Split out from `generate()` so the discovery/anchor-
 * extraction/duplicate-detection/locale-parity logic can be exercised
 * directly against a throwaway fixture directory in tests, independent of
 * HELP_ROOT and of writing the real help-index.json.
 *
 * @returns {{helpIds: Record<string, Record<string, string>>, duplicates: string[], incomplete: {id: string, missing: string[]}[]}}
 */
export function buildHelpIndex(root) {
  // Sorted for deterministic output and reproducible duplicate-detection
  // messages — readdir order is not guaranteed across filesystems.
  const files = findMarkdownFiles(root).sort()
  /** @type {Record<string, Record<string, string>>} */
  const helpIds = {}
  /** @type {Map<string, Set<string>>} locale -> set of help_ids seen in that locale */
  const seenPerLocale = new Map()
  const duplicates = []

  for (const absPath of files) {
    const relPath = relative(root, absPath)
    const { locale, routeParts } = localeAndRoutePath(relPath)
    const url = routePartsToUrl(routeParts)
    const ids = extractHelpIds(absPath)

    if (!seenPerLocale.has(locale)) seenPerLocale.set(locale, new Set())
    const seen = seenPerLocale.get(locale)

    for (const id of ids) {
      if (seen.has(id)) {
        duplicates.push(`duplicate help_id "${id}" in locale "${locale}" (${relPath})`)
        continue
      }
      seen.add(id)
      helpIds[id] ??= {}
      helpIds[id][locale] = `${url}#${id}`
    }
  }

  const allLocales = new Set(Object.values(LOCALE_DIRS))
  const incomplete = Object.entries(helpIds)
    .filter(([, byLocale]) => allLocales.difference(new Set(Object.keys(byLocale))).size > 0)
    .map(([id, byLocale]) => ({ id, missing: [...allLocales].filter((l) => !(l in byLocale)) }))

  return { helpIds, duplicates, incomplete }
}

/**
 * CLI-orchestration wrapper: runs buildHelpIndex(), logs locale-parity
 * warnings, writes help-index.json, and throws on duplicate help_ids
 * instead of calling process.exit() directly — only the bottom-of-file CLI
 * guard decides the process exit code, so this stays safely callable from
 * tests even for fixtures that deliberately contain a duplicate.
 */
export function generate(root = HELP_ROOT, outDir = join(root, 'public')) {
  const { helpIds, duplicates, incomplete } = buildHelpIndex(root)

  if (duplicates.length > 0) {
    throw new Error(
      ['generate-help-index: duplicate help_id(s) found — fix before building:', ...duplicates.map((d) => `  - ${d}`)].join('\n')
    )
  }

  if (incomplete.length > 0) {
    console.warn('generate-help-index: help_id(s) missing in at least one locale (non-blocking):')
    for (const { id, missing } of incomplete) {
      console.warn(`  - "${id}" missing in: ${missing.join(', ')}`)
    }
  }

  mkdirSync(outDir, { recursive: true })
  const outFile = join(outDir, 'help-index.json')
  writeFileSync(
    outFile,
    JSON.stringify({ generatedAt: new Date().toISOString(), helpIds }, null, 2) + '\n'
  )
  console.log(`generate-help-index: wrote ${Object.keys(helpIds).length} help_id(s) to ${relative(root, outFile)}`)
  return outFile
}

// Only run when executed directly (`node generate-help-index.mjs`), not when
// imported for unit testing (see generate-help-index.test.mjs).
//
// `--print` writes the computed index — including the duplicate and
// locale-parity findings that are only warnings here — to stdout as JSON
// instead of writing help-index.json. tools/check_help_contract.py consumes
// that so the CI gate resolves help_ids through this exact scan rather than a
// second, drift-prone reimplementation of it.
// Compared as decoded filesystem paths, not as URL text: import.meta.url
// percent-encodes characters that process.argv[1] leaves literal, so a
// checkout path containing a space (or any other such character) made the
// naive `file://${argv[1]}` comparison false and silently skipped this block.
const isDirectRun =
  process.argv[1] !== undefined && fileURLToPath(import.meta.url) === resolve(process.argv[1])

if (isDirectRun) {
  try {
    if (process.argv.includes('--print')) {
      process.stdout.write(JSON.stringify(buildHelpIndex(HELP_ROOT)) + '\n')
    } else {
      generate()
    }
  } catch (err) {
    console.error(err.message)
    process.exit(1)
  }
}
