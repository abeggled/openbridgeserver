<script setup lang="ts">
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { AccessLevel, DataPointValue, VisuNode } from '@/types'
import VisuIcon from '@/components/VisuIcon.vue'
import { useVisuStore } from '@/stores/visu'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()

const router   = useRouter()
const route    = useRoute()
const store    = useVisuStore()
const { t } = useI18n()
const label    = computed(() => (props.config.label         as string | undefined) ?? t('widgets.link.defaultLabel'))
const icon     = computed(() => (props.config.icon          as string | undefined) ?? '🔗')
const targetId  = computed(() => (props.config.target_node_id as string | undefined) ?? '')
const showIcon  = computed(() => props.config.show_icon !== false)
const preserveIconColor = computed(() => props.config.preserve_icon_color === true)
const labelSize = computed(() => {
  const s = props.config.label_size as string | undefined
  const map: Record<string, string> = { xs: 'text-xs', sm: 'text-sm', md: 'text-base', lg: 'text-lg', xl: 'text-xl' }
  return map[s ?? ''] ?? 'text-sm'
})
// Icon size in px; 0, missing or invalid keeps the default size (text-4xl).
const iconSize = computed(() => {
  const n = Number(props.config.icon_size)
  return Number.isFinite(n) && n > 0 ? n : 0
})
const showArrow = computed(() => props.config.show_arrow !== false)
// Back to the previously visited page, whichever it was; target_node_id is the
// fallback when there is no history (e.g. the page was opened directly).
const historyBack = computed(() => props.config.history_back === true)
// history.state is not reactive; it changes with every navigation, so the current
// route is read along with it. In the editor it says nothing about the Visu's history.
const canGoBack = computed(() => {
  void route.fullPath
  return !props.editorMode && !!window.history.state?.back
})
// Without history (bookmark, kiosk start, new tab) only the fallback page is a destination.
const hasDestination = computed(() => !!targetId.value || (historyBack.value && canGoBack.value))

const activeIndicator = computed(() => (props.config.active_indicator as string | undefined) ?? 'none')

function resolveAccessNode(node: VisuNode): { access: AccessLevel; definingId: string } {
  let cur: VisuNode | undefined = node
  while (cur) {
    if (cur.access !== null) return { access: cur.access, definingId: cur.id }
    cur = cur.parent_id ? store.getNode(cur.parent_id) : undefined
  }
  return { access: 'public', definingId: node.id }
}

function isVisibleInLocationOverview(node: VisuNode): boolean {
  const { access } = resolveAccessNode(node)
  if (access === 'user') return store.isLoggedIn
  return true
}

// Active if targetId is the current page OR any ancestor of the current page in the visu tree
const isActive = computed(() => {
  if (props.editorMode || !targetId.value) return false
  const currentId = route.params.id as string
  if (!currentId) return false
  let id: string | undefined = currentId
  while (id) {
    if (id === targetId.value) return true
    id = store.getNode(id)?.parent_id ?? undefined
  }
  return false
})

function navigate() {
  if (props.editorMode) return
  if (historyBack.value && window.history.state?.back) {
    router.back()
    return
  }
  if (!targetId.value) return
  const target = store.getNode(targetId.value)
  if (target?.type === 'LOCATION') {
    const targetAccess = resolveAccessNode(target)
    if (targetAccess.access === 'protected' && !store.hasSessionToken(targetAccess.definingId)) {
      router.push({ name: 'viewer', params: { id: targetId.value } })
      return
    }

    // Navigate to the first direct page that would also be visible in the location overview.
    const firstPage = store.getChildren(targetId.value)
      .find(n => n.type === 'PAGE' && isVisibleInLocationOverview(n))
    if (firstPage) {
      router.push({ name: 'viewer', params: { id: firstPage.id } })
      return
    }
  }
  router.push({ name: 'viewer', params: { id: targetId.value } })
}
</script>

<template>
  <div
    class="relative flex flex-col items-center justify-center h-full p-3 gap-2 select-none rounded-xl transition-colors"
    :class="[
      editorMode
        ? 'cursor-default opacity-70'
        : 'cursor-pointer hover:bg-gray-200/60 dark:hover:bg-white/5 active:bg-gray-300/60 dark:active:bg-white/10',
      isActive && activeIndicator === 'border' ? 'ring-2 ring-inset ring-[#D6A800]' : '',
    ]"
    @click="navigate"
  >
    <span
      v-if="showIcon"
      class="leading-none"
      :class="iconSize ? 'block w-full text-center' : 'text-4xl'"
      :style="iconSize ? { fontSize: `${iconSize}px` } : undefined"
      data-testid="link-icon"
    ><VisuIcon :icon="icon" :preserve-color="preserveIconColor" :fit-width="iconSize > 0" /></span>
    <span class="font-medium text-gray-800 dark:text-gray-200 text-center leading-tight" :class="labelSize">{{ label }}</span>
    <span v-if="showArrow && !editorMode && hasDestination" class="text-xs text-gray-400 dark:text-gray-500">→</span>
    <span v-else-if="!hasDestination" class="text-xs text-gray-400 dark:text-gray-600" data-testid="link-no-target">{{ $t('widgets.link.noTarget') }}</span>

    <!-- active_indicator: dot -->
    <span
      v-if="isActive && activeIndicator === 'dot'"
      class="text-lg leading-none"
      style="color: #D6A800"
      data-testid="link-active-dot"
    >●</span>

    <!-- active_indicator: bar (bottom line) -->
    <div
      v-if="isActive && activeIndicator === 'bar'"
      class="absolute bottom-0 left-2 right-2 h-0.5 rounded-full"
      style="background-color: #D6A800"
      data-testid="link-active-bar"
    />
  </div>
</template>
