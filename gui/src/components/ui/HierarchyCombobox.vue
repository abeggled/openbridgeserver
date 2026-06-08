<template>
  <Combobox
    :model-value="modelValue"
    :multi="true"
    :placeholder="effectivePlaceholder"
    :fetch-suggestions="fetchSuggestions"
    :display-items="displayItems"
    :empty-text="$t('common.noHierarchyNodes')"
    @update:modelValue="onUpdate"
  >
    <!-- Dropdown item: two-line (tree above path) -->
    <template #item="{ item }">
      <div
        class="flex flex-col min-w-0 flex-1"
        :style="{ paddingLeft: `${Math.min(item.display_indent || 0, 4) * 0.75}rem` }"
        v-bind="itemFullPathAttrs(item)"
      >
        <span v-if="!displayPathIncludesTree(item)" class="text-[10px] uppercase tracking-wide text-slate-400">{{ item.tree_name }}</span>
        <PathLabel :segments="itemDisplayPath(item)" />
      </div>
      <span v-if="item.is_leaf === false" class="text-xs text-slate-500 shrink-0">{{ $t('common.hierarchyNodeType') }}</span>
    </template>

    <!-- Chip: forward the consumer's slot first (FilterEditor injects an
         extra ⊞ expand affordance there), fall back to a plain PathLabel
         when no consumer slot is provided. Remove (×) is rendered by the
         surrounding Combobox wrapper. -->
    <template #chip="slotProps">
      <slot name="chip" v-bind="slotProps">
        <PathLabel :segments="itemDisplayPath(slotProps.item)" />
      </slot>
    </template>
  </Combobox>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Combobox from '@/components/ui/Combobox.vue'
import PathLabel from '@/components/ui/PathLabel.vue'
import { hierarchyApi } from '@/api/client'
import {
  hierarchyDisplayIndent,
  hierarchyDisplayPath,
  normalizeHierarchyDisplayDepth,
} from '@/utils/hierarchyDisplay'

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  placeholder: { type: String, default: null },
})
const emit = defineEmits(['update:modelValue'])

const effectivePlaceholder = computed(() => props.placeholder ?? t('common.hierarchySearchPlaceholder'))

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
  // UI display_depth is defined as: 0 = hierarchy name, 1 = first tree node,
  // 2 = second tree node, ... Node paths do not include the hierarchy name,
  // so the within-tree start index is display_depth - 1.
  const displayDepth = normalizeHierarchyDisplayDepth(tree?.display_depth)
  const startIndex = displayDepth - 1
  const treeHasDisplayDepth = displayDepth <= 0 || rawNodes.some((n) => pathOf(n).length > startIndex)

  return rawNodes.map((n) => {
    const path = pathOf(n)
    const displayPath = hierarchyDisplayPath({
      treeName: tree.name,
      path,
      displayDepth,
      treeHasDisplayDepth,
    })
    const fullPath = tree.name ? [tree.name, ...path] : path.slice()
    const label = displayPath.length ? displayPath.join(' › ') : path.join(' › ')
    const isDisplayable = !treeHasDisplayDepth || displayDepth <= 0 || path.length > startIndex

    return {
      id: `${tree.id}:${n.id}`,
      tree_id: tree.id,
      tree_name: tree.name,
      display_depth: displayDepth,
      display_path: displayPath,
      display_indent: hierarchyDisplayIndent(path, displayDepth),
      displayable: isDisplayable,
      node_id: n.id,
      path,
      full_path: fullPath,
      full_label: fullPath.join(' › '),
      is_leaf: !childIds.has(n.id),
      label,
    }
  })
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
const suggestionItems = computed(() => nodes.value.filter((n) => n.displayable !== false))

async function fetchSuggestions(q) {
  if (!nodes.value.length) await load()
  const needle = (q || '').toLowerCase().trim()
  const candidates = suggestionItems.value
  if (!needle) return candidates
  return candidates.filter((n) =>
    n.path.some((seg) => seg.toLowerCase().includes(needle)) ||
    n.display_path?.some((seg) => seg.toLowerCase().includes(needle)) ||
    (n.label || '').toLowerCase().includes(needle) ||
    (n.full_label || '').toLowerCase().includes(needle) ||
    (n.tree_name || '').toLowerCase().includes(needle),
  )
}

function onUpdate(val) {
  emit('update:modelValue', Array.isArray(val) ? val : [])
}

function itemDisplayPath(item) {
  return item?.display_path?.length ? item.display_path : (item?.path || [])
}

function displayPathIncludesTree(item) {
  const path = itemDisplayPath(item)
  return !!item?.tree_name && path[0] === item.tree_name
}

function itemFullPathAttrs(item) {
  const title = item?.full_label || itemDisplayPath(item).join(' › ')
  return title ? { title } : {}
}
</script>
