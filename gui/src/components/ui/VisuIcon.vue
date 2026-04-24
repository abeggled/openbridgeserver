<script setup>
import { computed, ref, watch, onMounted } from 'vue'
import { useIcons } from '@/composables/useIcons'

const props = defineProps({
  /** Either an emoji string ("🔗") or an SVG icon reference ("svg:{name}") */
  icon: { type: String, default: '' },
})

const { getSvg, isSvgIcon, svgIconName } = useIcons()

const isSvg = computed(() => isSvgIcon(props.icon))
const svgContent = ref('')

async function load() {
  if (!isSvg.value) { svgContent.value = ''; return }
  svgContent.value = await getSvg(svgIconName(props.icon))
}

onMounted(load)
watch(() => props.icon, load)
</script>

<template>
  <span v-if="!isSvg" class="leading-none">{{ icon }}</span>
  <span
    v-else-if="svgContent"
    class="inline-flex items-center justify-center w-[1em] h-[1em] [&>svg]:w-full [&>svg]:h-full brightness-0 dark:invert"
    v-html="svgContent"
  />
  <span v-else class="inline-block opacity-30">▪</span>
</template>
