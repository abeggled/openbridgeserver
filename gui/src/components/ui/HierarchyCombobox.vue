<template>
  <Combobox
    :model-value="modelValue"
    :multi="true"
    :placeholder="placeholder"
    :fetch-suggestions="fetchSuggestions"
    :display-items="displayItems"
    empty-text="Keine Knoten gefunden"
    @update:modelValue="onUpdate"
  >
    <!-- Dropdown item: two-line (tree above path) -->
    <template #item="{ item }">
      <div class="flex flex-col min-w-0 flex-1">
        <span class="text-[10px] uppercase tracking-wide text-slate-400">{{ item.tree_name }}</span>
        <PathLabel :segments="item.path" />
      </div>
      <span v-if="item.is_leaf === false" class="text-xs text-slate-500 shrink-0">Knoten</span>
    </template>

    <!-- Chip: forward the consumer's slot first (FilterEditor injects an
         extra ⊞ expand affordance there), fall back to a plain PathLabel
         when no consumer slot is provided. Remove (×) is rendered by the
         surrounding Combobox wrapper. -->
    <template #chip="slotProps">
      <slot name="chip" v-bind="slotProps">
        <PathLabel :segments="slotProps.item.path" />
      </slot>
    </template>
  </Combobox>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import Combobox from '@/components/ui/Combobox.vue'
import PathLabel from '@/components/ui/PathLabel.vue'
import { hierarchyApi } from '@/api/client'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  placeholder: { type: String, default: 'Knoten suchen …' },
})
const emit = defineEmits(['update:modelValue'])

/** All known nodes, fully built with path + tree info. */
const nodes = ref([])
const treesById = ref({})

function buildPathsForTree(tree, rawNodes) {
  const byId = new Map(rawNodes.map((n) => [n.id, n]))
  const cache = new Map()
  function pathOf(node) {
    if (cache.has(node.id)) return cache.get(node.id)
    const segs = []
    let cursor = node
    let guard = 0
    while (cursor && guard < 64) {
      segs.unshift(cursor.name)
      cursor = cursor.parent_id ? byId.get(cursor.parent_id) : null
      guard++
    }
    cache.set(node.id, segs)
    return segs
  }
  const childIds = new Set(rawNodes.filter((n) => n.parent_id).map((n) => n.parent_id))
  // display_depth (PR #462, issue #443) governs which path segment kicks off
  // the abbreviated tag in consumers like FilterEditor — kept on the item so
  // callers don't have to look the tree up again.
  const displayDepth = Number(tree?.display_depth) || 0
  return rawNodes.map((n) => ({
    id: `${tree.id}:${n.id}`,
    tree_id: tree.id,
    tree_name: tree.name,
    display_depth: displayDepth,
    node_id: n.id,
    path: pathOf(n),
    is_leaf: !childIds.has(n.id),
    label: pathOf(n).join(' › '),
  }))
}

/**
 * Backend /trees/{id}/nodes returns a *nested* tree (each node has a
 * `children` array). The path builder downstream wants a *flat* list with
 * `parent_id`, so we walk the nested response and produce that shape.
 */
function flattenNested(nested) {
  const out = []
  function walk(node) {
    out.push(node)
    if (Array.isArray(node.children)) {
      for (const child of node.children) walk(child)
    }
  }
  for (const root of nested || []) walk(root)
  return out
}

async function load() {
  try {
    const { data: trees } = await hierarchyApi.listTrees()
    if (!Array.isArray(trees)) return
    treesById.value = Object.fromEntries(trees.map((t) => [t.id, t]))
    const allNodes = []
    await Promise.all(
      trees.map(async (tree) => {
        try {
          const { data: tn } = await hierarchyApi.getTreeNodes(tree.id)
          if (Array.isArray(tn)) {
            const flat = flattenNested(tn)
            allNodes.push(...buildPathsForTree(tree, flat))
          }
        } catch {
          /* swallow per-tree errors */
        }
      }),
    )
    nodes.value = allNodes
  } catch {
    nodes.value = []
  }
}

onMounted(load)

const displayItems = computed(() => nodes.value)

async function fetchSuggestions(q) {
  if (!nodes.value.length) await load()
  const needle = (q || '').toLowerCase().trim()
  if (!needle) return nodes.value
  return nodes.value.filter((n) =>
    n.path.some((seg) => seg.toLowerCase().includes(needle)) ||
    (n.tree_name || '').toLowerCase().includes(needle),
  )
}

function onUpdate(val) {
  emit('update:modelValue', Array.isArray(val) ? val : [])
}
</script>
