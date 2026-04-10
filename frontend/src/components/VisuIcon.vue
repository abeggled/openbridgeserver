<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
import { useIcons } from '@/composables/useIcons'

const props = defineProps<{
  /** Either an emoji string (e.g. "🔗") or an SVG icon reference ("svg:{name}") */
  icon: string
}>()

const { getSvg, isSvgIcon, svgIconName } = useIcons()

const isSvg = computed(() => isSvgIcon(props.icon))
const svgContent = ref('')

async function load() {
  if (!isSvg.value) {
    svgContent.value = ''
    return
  }
  svgContent.value = await getSvg(svgIconName(props.icon))
}

onMounted(load)
watch(() => props.icon, load)
</script>

<template>
  <!-- Emoji icon -->
  <span v-if="!isSvg" class="leading-none">{{ icon }}</span>

  <!-- SVG icon: inline SVG scaled via CSS, black in light mode / white in dark mode -->
  <span
    v-else-if="svgContent"
    class="inline-flex items-center justify-center w-[1em] h-[1em] [&>svg]:w-full [&>svg]:h-full brightness-0 dark:invert"
    v-html="svgContent"
  />

  <!-- Placeholder while loading or on error -->
  <span v-else class="inline-block opacity-30">▪</span>
</template>
