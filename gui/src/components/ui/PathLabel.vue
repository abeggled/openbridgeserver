<template>
  <span
    ref="root"
    data-testid="pathlabel-root"
    class="inline-flex items-center gap-1 min-w-0 max-w-full"
    @pointerenter="showTooltip"
    @pointerleave="hideTooltip"
    @focusin="showTooltip"
    @focusout="hideTooltip"
  >
    <template v-for="(seg, i) in visibleSegments" :key="`${i}-${seg.kind}`">
      <span
        v-if="i > 0"
        class="text-slate-400 dark:text-slate-500 shrink-0 select-none"
        aria-hidden="true"
      >{{ separator }}</span>
      <span
        :class="[
          'truncate',
          seg.kind === 'ellipsis' ? 'text-slate-400 shrink-0' : '',
          i === visibleSegments.length - 1 && seg.kind === 'segment'
            ? 'font-medium text-slate-800 dark:text-slate-100'
            : 'text-slate-500 dark:text-slate-400',
        ]"
      >{{ seg.text }}</span>
    </template>

    <span
      v-if="tooltipOpen && fullText"
      ref="floating"
      data-testid="pathlabel-tooltip"
      class="z-50 px-2 py-1 rounded-md bg-slate-800 text-white text-xs shadow-lg pointer-events-none whitespace-nowrap"
      :style="floatingStyles"
      role="tooltip"
    >{{ fullText }}</span>
  </span>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useFloating, autoUpdate, offset, flip, shift } from '@floating-ui/vue'

const props = defineProps({
  segments: { type: Array, default: () => [] },
  separator: { type: String, default: '›' },
  hideRoot: { type: Boolean, default: false },
  /**
   * Approximate width (in px) each segment is allowed before we start
   * collapsing. Exposed for tests; default is a reasonable heuristic.
   */
  segmentMinPx: { type: Number, default: 40 },
})

const root = ref(null)
const floating = ref(null)
const tooltipOpen = ref(false)
const containerWidth = ref(0)
let ro = null
let stopAutoUpdate = null

const effectiveSegments = computed(() => {
  if (!props.segments?.length) return []
  if (props.hideRoot && props.segments.length > 1) return props.segments.slice(1)
  return props.segments.slice()
})

const fullText = computed(() => effectiveSegments.value.join(` ${props.separator} `))

/**
 * Compute which segments fit. Returns a list of { kind, text } entries
 * where kind is 'segment' or 'ellipsis'.
 *
 * Strategy:
 *   - measure container width and the number of segments
 *   - estimate how many segments we can show given segmentMinPx + separator
 *   - if all fit: show all
 *   - else if at least 2 fit: keep first + last, drop middle into '…'
 *   - else: only leaf with '…' prefix
 */
const visibleSegments = computed(() => {
  const segs = effectiveSegments.value
  if (!segs.length) return []
  const width = containerWidth.value
  // Width 0 means "not measured yet" → assume plenty of space.
  if (!width) return segs.map((s) => ({ kind: 'segment', text: s }))

  const px = Math.max(props.segmentMinPx, 1)
  const capacity = Math.max(1, Math.floor(width / px))

  if (segs.length <= capacity) {
    return segs.map((s) => ({ kind: 'segment', text: s }))
  }
  if (capacity >= 2) {
    return [
      { kind: 'segment', text: segs[0] },
      { kind: 'ellipsis', text: '…' },
      { kind: 'segment', text: segs[segs.length - 1] },
    ]
  }
  return [
    { kind: 'ellipsis', text: '…' },
    { kind: 'segment', text: segs[segs.length - 1] },
  ]
})

// Floating UI positioning
const { floatingStyles, update } = useFloating(root, floating, {
  placement: 'top',
  middleware: [offset(6), flip(), shift({ padding: 8 })],
})

function startAutoUpdate() {
  if (root.value && floating.value && !stopAutoUpdate) {
    stopAutoUpdate = autoUpdate(root.value, floating.value, update)
  }
}

function stopAutoUpdateFn() {
  if (stopAutoUpdate) {
    stopAutoUpdate()
    stopAutoUpdate = null
  }
}

function showTooltip() {
  if (!fullText.value) return
  tooltipOpen.value = true
  nextTick(() => startAutoUpdate())
}

function hideTooltip() {
  tooltipOpen.value = false
  stopAutoUpdateFn()
}

function measure() {
  if (!root.value) return
  containerWidth.value = root.value.clientWidth || root.value.offsetWidth || 0
}

onMounted(() => {
  if (!root.value) return
  measure()
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => measure())
    ro.observe(root.value)
  }
})

onBeforeUnmount(() => {
  if (ro) {
    ro.disconnect()
    ro = null
  }
  stopAutoUpdateFn()
})

watch(() => props.segments, () => {
  // re-measure on next tick after DOM update
  nextTick(measure)
})
</script>
