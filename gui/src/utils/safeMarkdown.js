// Minimal Markdown parser for externally loaded release notes (issue #1370).
//
// It never produces HTML. The result is a plain block/inline tree that
// SafeMarkdown.vue renders through Vue's render function, so every piece of
// text ends up as an escaped text node: embedded HTML, <script> tags and event
// handler attributes stay visible as literal text and are never executed.
// Links are only kept for absolute http(s) URLs; anything else (javascript:,
// data:, relative paths) is rendered as its label without a link.
//
// Supported subset — what the GitHub release bodies of OBS use: ATX headings,
// bullet and numbered lists, fenced code blocks, paragraphs, `code`, **bold**,
// [label](url) links and bare http(s) URLs. HTML comments are dropped.

const FENCE_RE = /^\s*(```|~~~)/
// Linear on any input: the closing # sequence is stripped by headingText(), not
// here — a lazy text group before optional whitespace backtracks quadratically.
const HEADING_RE = /^(#{1,6})[ \t]+(.*)$/
const BULLET_RE = /^\s*[-*+]\s+(.*)$/
const ORDERED_RE = /^\s*\d{1,9}[.)]\s+(.*)$/
// Bounded repetitions keep the scan for an unmatched `, ** or [ short, and the
// global regex is advanced through lastIndex instead of re-slicing, so a
// long paragraph stays linear (release bodies may be ~125k characters).
// Like GitHub, __ never emphasises inside a word: snake__case__name stays text.
const INLINE_RE = /(`[^`\n]{1,500}`)|(\*\*[^*\n]{1,500}\*\*|(?<![\p{L}\p{N}_])__[^_\n]{1,500}__(?![\p{L}\p{N}_]))|(\[[^\]\n]{1,500}\]\((?:[^()\s]|\([^()\s]{0,500}\)){1,2000}\))|(https?:\/\/[^\s<>()]{1,2000}[^\s<>().,;:!?'"])/gu

export function safeHref(url) {
  if (typeof url !== 'string') return null
  const trimmed = url.trim()
  if (!/^https?:\/\//i.test(trimmed)) return null
  try {
    // The scheme test above already pinned http(s); parsing normalises the URL.
    return new URL(trimmed).href
  } catch {
    return null
  }
}

export function parseInline(text) {
  const nodes = []
  let pos = 0
  INLINE_RE.lastIndex = 0
  while (pos < text.length) {
    const match = INLINE_RE.exec(text)
    if (!match) {
      nodes.push({ type: 'text', text: text.slice(pos) })
      break
    }
    if (match.index > pos) nodes.push({ type: 'text', text: text.slice(pos, match.index) })
    const [token, code, strong, link, bare] = match
    if (code) {
      nodes.push({ type: 'code', text: code.slice(1, -1) })
    } else if (strong) {
      nodes.push({ type: 'strong', text: strong.slice(2, -2) })
    } else if (link) {
      const split = link.indexOf('](')
      const label = link.slice(1, split)
      const href = safeHref(link.slice(split + 2, -1))
      nodes.push(href ? { type: 'link', text: label, href } : { type: 'text', text: label })
    } else {
      const href = safeHref(bare)
      nodes.push(href ? { type: 'link', text: bare, href } : { type: 'text', text: bare })
    }
    pos = match.index + token.length
  }
  return nodes
}

// A closing # sequence only counts after whitespace (CommonMark): `## C#` keeps its #.
function headingText(raw) {
  const text = raw.trimEnd()
  let end = text.length
  while (end > 0 && text[end - 1] === '#') end -= 1
  if (end === text.length || (end > 0 && !/\s/.test(text[end - 1]))) return text
  return text.slice(0, end).trimEnd()
}

export function parseMarkdown(source) {
  if (typeof source !== 'string' || !source) return []
  const lines = source.replace(/<!--[\s\S]*?(-->|$)/g, '').replace(/\r\n?/g, '\n').split('\n')
  const blocks = []
  let paragraph = []
  let list = null

  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ type: 'paragraph', children: parseInline(paragraph.join(' ')) })
    paragraph = []
  }
  const flushList = () => {
    if (list) blocks.push(list)
    list = null
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (FENCE_RE.test(line)) {
      flushParagraph()
      flushList()
      const fence = line.trim().slice(0, 3)
      const code = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith(fence)) code.push(lines[i++])
      blocks.push({ type: 'code', text: code.join('\n') })
      continue
    }
    const heading = HEADING_RE.exec(line)
    if (heading) {
      flushParagraph()
      flushList()
      blocks.push({ type: 'heading', level: heading[1].length, children: parseInline(headingText(heading[2])) })
      continue
    }
    const bullet = BULLET_RE.exec(line)
    const ordered = bullet ? null : ORDERED_RE.exec(line)
    if (bullet || ordered) {
      flushParagraph()
      const isOrdered = Boolean(ordered)
      if (!list || list.ordered !== isOrdered) {
        flushList()
        list = { type: 'list', ordered: isOrdered, items: [] }
      }
      list.items.push(parseInline((bullet || ordered)[1]))
      continue
    }
    if (!line.trim()) {
      flushParagraph()
      flushList()
      continue
    }
    if (list) {
      // A wrapped continuation line of the previous list item.
      const last = list.items[list.items.length - 1]
      last.push({ type: 'text', text: ' ' }, ...parseInline(line.trim()))
      continue
    }
    paragraph.push(line.trim())
  }
  flushParagraph()
  flushList()
  return blocks
}
