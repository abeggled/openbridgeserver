<script>
// Renders untrusted Markdown (release notes from GitHub, issue #1370) without
// v-html: parseMarkdown() returns a plain tree and every string becomes an
// escaped text node here. See utils/safeMarkdown.js for the supported subset.
import { computed, h } from 'vue'
import { parseMarkdown } from '@/utils/safeMarkdown'

const HEADING_CLASSES = {
  1: 'text-base font-semibold text-slate-800 dark:text-slate-100 mt-3',
  2: 'text-base font-semibold text-slate-800 dark:text-slate-100 mt-3',
  3: 'text-sm font-semibold text-slate-800 dark:text-slate-100 mt-3',
}
const SMALL_HEADING_CLASS = 'text-sm font-medium text-slate-700 dark:text-slate-200 mt-2'

function renderInline(nodes) {
  return nodes.map((node) => {
    if (node.type === 'code') {
      return h('code', { class: 'px-1 py-0.5 rounded bg-slate-100 dark:bg-slate-800 font-mono text-xs' }, node.text)
    }
    if (node.type === 'strong') return h('strong', node.text)
    if (node.type === 'link') {
      return h('a', {
        href: node.href,
        target: '_blank',
        rel: 'noopener noreferrer nofollow',
        class: 'text-blue-500 hover:underline break-all',
      }, node.text)
    }
    return node.text
  })
}

function renderBlock(block, index) {
  if (block.type === 'heading') {
    const level = Math.min(block.level + 2, 6)
    return h(`h${level}`, { key: index, class: HEADING_CLASSES[block.level] ?? SMALL_HEADING_CLASS }, renderInline(block.children))
  }
  if (block.type === 'list') {
    return h(
      block.ordered ? 'ol' : 'ul',
      { key: index, class: [block.ordered ? 'list-decimal' : 'list-disc', 'pl-5 flex flex-col gap-1'] },
      block.items.map((item, itemIndex) => h('li', { key: itemIndex }, renderInline(item))),
    )
  }
  if (block.type === 'code') {
    return h('pre', { key: index, class: 'p-2 rounded bg-slate-100 dark:bg-slate-800 font-mono text-xs overflow-x-auto' }, block.text)
  }
  return h('p', { key: index }, renderInline(block.children))
}

export default {
  name: 'SafeMarkdown',
  props: {
    source: { type: String, default: '' },
  },
  setup(props) {
    const blocks = computed(() => parseMarkdown(props.source))
    return () => h('div', { class: 'flex flex-col gap-2 text-sm text-slate-600 dark:text-slate-300 break-words' }, blocks.value.map(renderBlock))
  },
}
</script>
