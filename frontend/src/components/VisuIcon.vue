<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useIcons } from '@/composables/useIcons'

const props = defineProps<{
  /** Either an emoji string (e.g. "🔗") or an SVG icon reference ("svg:{name}") */
  icon: string
  /** When true, imported SVGs are rendered with their original fill colors instead of brightness-0/invert */
  preserveColor?: boolean
}>()

const { getSvg, isSvgIcon, svgIconName } = useIcons()

const isSvg = computed(() => isSvgIcon(props.icon))
const svgBlobUrl = ref('')
let loadToken = 0

function resetBlobUrl() {
  if (svgBlobUrl.value) {
    URL.revokeObjectURL(svgBlobUrl.value)
    svgBlobUrl.value = ''
  }
}

async function load() {
  const token = ++loadToken
  resetBlobUrl()
  if (!isSvg.value) return

  const svgContent = await getSvg(svgIconName(props.icon))
  if (token !== loadToken) return
  if (!svgContent) return

  const blob = new Blob([svgContent], { type: 'image/svg+xml' })
  svgBlobUrl.value = URL.createObjectURL(blob)
}

onMounted(load)
watch(() => props.icon, load)
onBeforeUnmount(() => {
  loadToken += 1
  resetBlobUrl()
})
</script>

<template>
  <!-- Emoji icon -->
  <span v-if="!isSvg" class="leading-none">{{ icon }}</span>

  <!-- SVG icon rendered as image to avoid executing untrusted SVG scripts -->
  <img
    v-else-if="svgBlobUrl"
    :src="svgBlobUrl"
    alt=""
    class="inline-block w-[1em] h-[1em] object-contain"
    :class="preserveColor ? '' : 'brightness-0 dark:invert'"
    loading="lazy"
    decoding="async"
  />

  <!-- Placeholder while loading or on error -->
  <span v-else class="inline-block opacity-30">▪</span>
</template>
