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
// ATX (`## Title {#id}`, optionally with CommonMark's closing hash sequence)
// and Setext (`Title {#id}` over `===`/`---`), each optionally inside a
// blockquote or list item — all of these VitePress renders with the explicit
// id, verified against a real build.
// A blockquote or list-item prefix. Both a heading and a fence can sit
// inside one, and CommonMark keeps their meaning there.
// CommonMark makes the space after a blockquote marker optional, so `>##` is
// the same heading as `> ##`. A list marker takes one to four spaces: with
// five or more, only the first belongs to the marker and the rest indent an
// code block, so `-     ## x` is code and not a heading.
const CONTAINER = String.raw` {0,3}(?:>[^\S\r\n]*|(?:[-*+]|\d{1,9}[.)])[^\S\r\n]{1,4}(?![^\S\r\n]))*`

// `[^\S\r\n]` rather than `\s`: the space after the hashes has to be on the
// heading's own line, or an empty `##` would swallow the next line's prose.
// `(?<!\\)(?:\\\\)*` counts escape parity — `\{#id}` is escaped text, while
// `\\{#id}` is a backslash followed by a live anchor.
const UNESCAPED = String.raw`(?<!\\)(?:\\\\)*`
// The id class mirrors the Python validator's `^[A-Za-z][\w-]*$`, but JS `\w`
// is ASCII-only while Python's is Unicode-aware — so `{#widget-tür}` passed
// validation and was then missing from the index, and the reverse render check
// rejected a heading VitePress does emit (verified against a real build:
// `<h2 id="widget-tür">`). Spelled out with Unicode property escapes, which
// need the `u` flag.
const HELP_ID = String.raw`[A-Za-z][\p{L}\p{N}\p{M}_-]*`
const HEADING_RE = new RegExp(
  String.raw`^(?:${CONTAINER}|[^\S\r\n]*)#{1,6}[^\S\r\n]+.*${UNESCAPED}\{#(${HELP_ID})\}(?:[^\S\r\n]+#*)?[^\S\r\n]*$` +
    String.raw`|^${CONTAINER}\S.*${UNESCAPED}\{#(${HELP_ID})\}[^\S\r\n]*\r?\n(?:${CONTAINER}|[^\S\r\n]*)(?:=+|-+)[^\S\r\n]*$`,
  'gmu'
)

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

// A fence can sit inside a blockquote or list item just as a heading can; the
// same container prefix has to be allowed, or the heading regex would index a
// line the fence keeps as code.
const FENCE_RE = new RegExp(String.raw`^${CONTAINER}(\`{3,}|~{3,})(.*)$`)

/**
 * Blank out fenced code blocks, keeping the line count so nothing downstream
 * shifts. An anchor-shaped heading inside a fence — a documentation example
 * showing how to write one — is rendered by VitePress as `<code>` text and
 * owns no DOM id, so indexing it would hand the Admin-GUI a help_id that
 * resolves to a fragment no element answers to.
 */
/** The chain of containers a line opens: `> - ` is a quote holding a list. */
function containerOf(line) {
  const chain = []
  let rest = line
  let column = 0
  for (;;) {
    const quote = /^( {0,3})>([^\S\r\n]?)/.exec(rest)
    if (quote) {
      chain.push({ kind: 'quote' })
      column += quote[0].length
      rest = rest.slice(quote[0].length)
      continue
    }
    const item = /^( {0,3})(?:[-*+]|\d{1,9}[.)])([^\S\r\n]+)/.exec(rest)
    if (item) {
      chain.push({ kind: 'list', indent: column + item[0].length })
      column += item[0].length
      rest = rest.slice(item[0].length)
      continue
    }
    break
  }
  return chain.length > 0 ? chain : null
}

/** Does `line` still belong to every container in `chain`?
 *
 * A blank line ends neither; anything else has to satisfy each level, so a
 * fence opened in `> - ` closes as soon as the quote *or* the item does.
 */
function continuesContainer(line, chain) {
  if (line.trim() === '') return true
  let rest = line
  for (const container of chain) {
    if (container.kind === 'quote') {
      const quote = /^ {0,3}>[^\S\r\n]?/.exec(rest)
      if (!quote) return false
      rest = rest.slice(quote[0].length)
      continue
    }
    // Columns, not characters: a tab advances to the next multiple of four,
    // so a tabbed continuation still sits inside the list.
    if (columnWidth(line.slice(0, line.length - rest.length)) + columnWidth(/^[^\S\r\n]*/.exec(rest)[0]) < container.indent) return false
  }
  return true
}

export function stripFencedCode(text) {
  let fence = null
  return text
    .split('\n')
    .map((line) => {
      // A fence opened inside a container ends with that container: a
      // blockquote closes it at the first unquoted line, a list item at the
      // first line indented less than its content. VitePress resumes parsing
      // there, so keeping the fence open would blank real headings below it.
      if (fence !== null && fence.container !== null && !continuesContainer(line, fence.container)) fence = null
      const match = FENCE_RE.exec(line)
      if (match) {
        const marker = match[1]
        const char = marker[0]
        if (fence === null) {
          // A backtick fence's info string may not itself contain a backtick
          // (CommonMark) — that is inline code, not a fence.
          if (!(char === '`' && match[2].includes('`'))) {
            fence = { char, length: marker.length, container: containerOf(line) }
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
export function stripHtmlComments(text, protectedRanges = []) {
  const inside = (index) => protectedRanges.some(([from, to]) => index >= from && index < to)
  return text.replace(/<!--[\s\S]*?-->/g, (match, offset) =>
    // An opener inside fenced code is inert text, not the start of a comment:
    // pairing it with a `-->` after the fence would erase the real Markdown
    // between them.
    inside(offset) ? match : match.replace(/[^\n]/g, '')
  )
}

/** The character ranges fenced code occupies, for protecting them from other passes. */
export function fencedRanges(text) {
  // Derived from stripFencedCode itself, so both agree on where a fence begins
  // and ends: a line the stripper blanks is a line inside a fence.
  const ranges = []
  const blanked = stripFencedCode(text).split('\n')
  const original = text.split('\n')
  let cursor = 0
  let start = null
  for (let index = 0; index < original.length; index += 1) {
    const isFenced = blanked[index] === '' && original[index] !== ''
    if (isFenced && start === null) start = cursor
    if (!isFenced && start !== null) {
      ranges.push([start, cursor])
      start = null
    }
    cursor += original[index].length + 1
  }
  if (start !== null) ranges.push([start, cursor])
  return ranges
}

// A tag name, not an autolink: `<https://example.com>` must stay a paragraph.
// CommonMark's block-tag list (condition 6). An *inline* tag — `<span>`,
// `<a>`, `<b>` — starts a paragraph, not an HTML block, and a heading on the
// next line is still rendered.
const HTML_BLOCK_TAGS = [
  'address','article','aside','base','basefont','blockquote','body','caption','center','col','colgroup','dd','details','dialog','dir','div','dl','dt','fieldset','figcaption','figure','footer','form','frame','frameset','h1','h2','h3','h4','h5','h6','head','header','hr','html','iframe','legend','li','link','main','menu','menuitem','nav','noframes','ol','optgroup','option','p','param','search','section','summary','table','tbody','td','tfoot','th','thead','title','tr','track','ul',
]
const HTML_BLOCK_START_RE = new RegExp(String.raw`^${CONTAINER}<\/?(?:${HTML_BLOCK_TAGS.join('|')})(?=[\s/>]|$)`, 'i')
// CommonMark condition 7: a *complete* tag alone on its line opens a block for
// any element, including a custom one like `<x-review>`. An inline tag with
// text after it does not, which is what keeps `<span>x</span>` a paragraph.
const HTML_CUSTOM_BLOCK_START_RE = new RegExp(
  String.raw`^${CONTAINER}<\/?[A-Za-z][A-Za-z0-9-]*(?:\s[^>]*)?\/?>[^\S\r\n]*$`
)
// CommonMark "type 1": these run to their closing tag rather than to the next
// blank line, so markdown inside them is never parsed.
const LITERAL_BLOCK_START_RE = new RegExp(String.raw`^${CONTAINER}<(pre|script|style|textarea)(?=[\s/>]|$)`, 'i')
// CommonMark conditions 3, 4 and 5: a processing instruction, a declaration
// (`<!REVIEW`, `<!DOCTYPE`) and a CDATA section. Like type 1 they run to their
// own terminator rather than to the next blank line — verified against a real
// build: a heading inside `<!REVIEW … >` renders no id. Each entry pairs the
// opener with the string that closes it.
const DELIMITED_BLOCK_STARTS = [
  [new RegExp(String.raw`^${CONTAINER}<\?`), '?>'],
  [new RegExp(String.raw`^${CONTAINER}<!\[CDATA\[`), ']]>'],
  [new RegExp(String.raw`^${CONTAINER}<![A-Za-z]`), '>'],
]

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
  let terminator = null // a type 3/4/5 block, closed by a fixed string
  let container = null
  return text
    .split('\n')
    .map((line) => {
      // Leaving the container ends the block, exactly as it ends a fence.
      if (inBlock && container !== null && !continuesContainer(line, container)) {
        inBlock = false
        literalTag = null
        terminator = null
        container = null
      }
      if (!inBlock) {
        const literal = LITERAL_BLOCK_START_RE.exec(line)
        if (literal) {
          inBlock = true
          literalTag = literal[1].toLowerCase()
          container = containerOf(line)
        } else if (HTML_BLOCK_START_RE.test(line) || HTML_CUSTOM_BLOCK_START_RE.test(line)) {
          inBlock = true
          container = containerOf(line)
        } else {
          for (const [start, closes] of DELIMITED_BLOCK_STARTS) {
            if (start.test(line)) {
              inBlock = true
              terminator = closes
              container = containerOf(line)
              break
            }
          }
        }
      }
      if (!inBlock) return line
      if (terminator !== null) {
        if (line.includes(terminator)) {
          inBlock = false
          terminator = null
        }
        return ''
      }
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
        container = null
        return line
      }
      return ''
    })
    .join('\n')
}

// VitePress strips a leading BOM before parsing, so one may precede the `---`.
const FRONTMATTER_RE = /^\uFEFF?---\r?\n[\s\S]*?\r?\n---[^\S\r\n]*(\r?\n|$)/

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

/**
 * A page's Markdown with every region the site does not render blanked out.
 * Shared with tools/check_help_contract.py so both agree on what counts as
 * present at all — its reverse check applies its *own* heading detection to
 * this, which is what keeps it able to catch a heading form HEADING_RE misses.
 */
export function strippedSource(text) {
  // Neither pass may run first unconditionally: a ``` inside `<!-- … -->` is
  // commented-out text, and a `<!--` inside fenced code is inert. Comments are
  // stripped first, but skipping any opener that lies inside a fence — so both
  // hold at once.
  const withoutFrontmatter = stripFrontmatter(text)
  const withoutComments = stripHtmlComments(withoutFrontmatter, fencedRanges(withoutFrontmatter))
  return stripRawHtmlBlocks(stripFencedCode(withoutComments))
}

/** Every page's stripped source, keyed like the rendered pages. */
export function strippedSources(root = HELP_ROOT) {
  const sources = {}
  for (const absPath of findMarkdownFiles(root).sort()) {
    const page = relative(root, absPath).split(sep).join('/').replace(/\.md$/, '.html')
    sources[page] = strippedSource(readFileSync(absPath, 'utf-8'))
  }
  return sources
}

const CONTAINER_PREFIX_RE = new RegExp(String.raw`^${CONTAINER}`)

/**
 * The content column each line may be indented to, given the list items open
 * at that point. A heading may sit up to three columns past it; further in it
 * is an indented code block.
 *
 * Line-by-line block context, which a single regex cannot carry: `    ## x` is
 * code at the top level but a heading inside two nested list items.
 */
function contentColumnByLine(text) {
  const columns = []
  const open = []
  for (const line of text.split('\n')) {
    const indent = columnWidth(/^[^\S\r\n]*/.exec(line)[0])
    if (line.trim() !== '') {
      while (open.length > 0 && indent < open[open.length - 1]) open.pop()
    }
    columns.push(open.length > 0 ? open[open.length - 1] : 0)
    // Only a list item opens a content column: a blockquote's content is
    // marked by repeating `>`, not by indentation, so counting it here would
    // let an indented code block below a quote pass as a heading.
    const prefix = CONTAINER_PREFIX_RE.exec(line)?.[0] ?? ''
    if (/[-*+]|\d[.)]/.test(prefix)) open.push(columnWidth(prefix))
  }
  return columns
}

/** Column of a match's start within `text`, and the line it sits on. */
function lineOfOffset(text, offset) {
  let line = 0
  for (let index = 0; index < offset; index += 1) if (text[index] === '\n') line += 1
  return line
}

function underlineIsIndentedLegally(matched) {
  const [heading, underline] = matched.split(/\r?\n/)
  const headingPrefix = CONTAINER_PREFIX_RE.exec(heading)?.[0] ?? ''
  // Deliberately no check that the underline repeats the heading's blockquote
  // marker: CommonMark would call that a paragraph plus a thematic break, but
  // VitePress renders `> Title {#id}` over an unquoted `---` *with* the id
  // (verified against a real build), and the index has to match what ships.
  //
  // Measured in columns from the start of the line — a tab counts as the four
  // it renders as — so the underline may sit at most three past the column its
  // heading's content starts at.
  const contentColumn = columnWidth(headingPrefix)
  const underlineColumn = columnWidth(/^[^-=]*/.exec(underline)[0])
  return underlineColumn <= contentColumn + 3
}

/** Width in columns, with a tab advancing to the next multiple of four. */
function columnWidth(text) {
  let column = 0
  for (const character of text) column = character === '\t' ? column + 4 - (column % 4) : column + 1
  return column
}

function extractHelpIds(absPath) {
  const text = strippedSource(readFileSync(absPath, 'utf-8'))
  const ids = []
  const contentColumns = contentColumnByLine(text)
  for (const match of text.matchAll(HEADING_RE)) {
    // A Setext underline may be indented up to three columns past the content
    // column its heading sits at; beyond that CommonMark makes it code, and
    // VitePress renders no heading (verified against a real build).
    if (match[2] !== undefined && !underlineIsIndentedLegally(match[0])) continue
    // The same bound for an ATX heading, measured against the list items open
    // at its line: `    ## x` is code at the top level, a heading two items in.
    if (match[1] !== undefined) {
      const indent = columnWidth(/^[^\S\r\n]*/.exec(match[0])[0])
      if (indent > contentColumns[lineOfOffset(text, match.index)] + 3) continue
    }
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
    } else if (process.argv.includes('--stripped')) {
      process.stdout.write(JSON.stringify(strippedSources(HELP_ROOT)) + '\n')
    } else {
      generate()
    }
  } catch (err) {
    console.error(err.message)
    process.exit(1)
  }
}
