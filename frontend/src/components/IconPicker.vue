<script setup lang="ts">
/**
 * IconPicker — reusable icon selector
 *
 * Shows emoji quick-picks and, when SVG icons are available in the icon library,
 * a second "Icons" tab listing all imported SVG icons.
 *
 * v-model: string — either an emoji ("🔗") or an SVG reference ("svg:{name}")
 *
 * @prop dark  Force dark-mode styling (for widget config panels that are always dark)
 */
import { ref, computed, watch, onMounted } from 'vue'
import { useIcons } from '@/composables/useIcons'
import VisuIcon from '@/components/VisuIcon.vue'

const props = defineProps<{
  modelValue: string
  dark?: boolean
}>()

const emit = defineEmits<{ (e: 'update:modelValue', val: string): void }>()

const QUICK_ICONS = [
  '🏠','🏡','📁','📄','⚡','💡','🌡️','🔒','🚿','🛁','🛋️','🍳','🛏️',
  '🪟','🚪','🌿','🔆','🔅','💧','🌬️','🎵','📹','🔔','🌞','🌙','🎛️','🎚️','🔌','🌐','⚙️',
  '🔗','🏊','🌳','🚗','🔑','📊','🔋','🏭','☀️','🌬️',
]

const { iconNames, loadList, isSvgIcon } = useIcons()
const tab = ref<'emoji' | 'svg'>('emoji')
const search = ref('')

// Switch to svg tab when current value is an SVG icon
watch(
  () => props.modelValue,
  (val) => { if (isSvgIcon(val)) tab.value = 'svg' },
  { immediate: true },
)

const filteredSvg = computed(() => {
  const q = search.value.toLowerCase().trim()
  return q ? iconNames.value.filter((n) => n.includes(q)) : iconNames.value
})

function selectEmoji(ic: string) {
  tab.value = 'emoji'
  emit('update:modelValue', ic)
}

function selectSvg(name: string) {
  emit('update:modelValue', `svg:${name}`)
}

function onCustomInput(e: Event) {
  emit('update:modelValue', (e.target as HTMLInputElement).value)
}

onMounted(loadList)

// ── Styling helpers (dark vs. light mode) ─────────────────────────────────────
// When `dark` prop is true the component forces dark styles (always-dark panels
// like Energiefluss Config). Otherwise standard Tailwind dark: prefix is used.

const tabBase = 'rounded px-2 py-0.5 text-xs font-medium transition-colors'
const tabActive = computed(() =>
  props.dark
    ? `${tabBase} bg-blue-500/20 text-blue-300`
    : `${tabBase} bg-blue-500/10 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400`,
)
const tabInactive = computed(() =>
  props.dark
    ? `${tabBase} text-gray-500 hover:text-gray-300`
    : `${tabBase} text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300`,
)
const inputCls = computed(() =>
  props.dark
    ? 'bg-gray-800 border border-gray-700 text-gray-100 focus:border-blue-500'
    : 'bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 text-gray-900 dark:text-gray-100 focus:border-blue-500',
)
const btnIconCls = computed(() =>
  props.dark ? 'hover:bg-gray-700' : 'hover:bg-gray-100 dark:hover:bg-gray-700',
)
const btnIconActiveCls = computed(() =>
  props.dark
    ? 'ring-2 ring-blue-500 bg-blue-500/20'
    : 'ring-2 ring-blue-500 bg-blue-50 dark:bg-blue-500/20',
)
const svgBtnBase = computed(() =>
  props.dark
    ? 'border border-gray-700 hover:border-gray-500 rounded p-1 transition-colors'
    : 'border border-gray-200 dark:border-gray-700 hover:border-gray-400 dark:hover:border-gray-500 rounded p-1 transition-colors',
)
const svgBtnActiveCls = computed(() =>
  props.dark
    ? 'border-blue-500 bg-blue-500/20'
    : 'border-blue-500 bg-blue-50 dark:bg-blue-500/20',
)
</script>

<template>
  <div class="space-y-2">
    <!-- ── Tabs ──────────────────────────────────────────────────────────── -->
    <div class="flex gap-1">
      <button :class="tab === 'emoji' ? tabActive : tabInactive" type="button" @click="tab = 'emoji'">Emoji</button>
      <button
        v-if="iconNames.length"
        :class="tab === 'svg' ? tabActive : tabInactive"
        type="button"
        @click="tab = 'svg'"
      >
        Icons ({{ iconNames.length }})
      </button>
    </div>

    <!-- ── Emoji tab ─────────────────────────────────────────────────────── -->
    <template v-if="tab === 'emoji'">
      <!-- Free-text emoji input + clear button -->
      <div class="flex items-center gap-1">
        <input
          :value="isSvgIcon(modelValue) ? '' : (modelValue ?? '')"
          type="text"
          maxlength="4"
          placeholder="Emoji"
          class="w-14 text-center rounded px-2 py-1 text-lg focus:outline-none"
          :class="inputCls"
          @input="onCustomInput"
        />
        <button
          v-if="modelValue"
          type="button"
          class="text-xs px-1.5 py-1 rounded transition-colors"
          :class="props.dark ? 'text-gray-500 hover:text-gray-300 hover:bg-gray-700' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'"
          title="Icon entfernen"
          @click="emit('update:modelValue', '')"
        >✕</button>
      </div>
      <!-- Quick picks grid (max 3 rows, scrollable) -->
      <div class="flex flex-wrap gap-1 max-h-24 overflow-y-auto">
        <button
          v-for="ic in QUICK_ICONS"
          :key="ic"
          type="button"
          class="w-7 h-7 text-base flex items-center justify-center rounded transition-colors"
          :class="[btnIconCls, modelValue === ic ? btnIconActiveCls : '']"
          :title="ic"
          @click="selectEmoji(ic)"
        >{{ ic }}</button>
      </div>
    </template>

    <!-- ── SVG icons tab ─────────────────────────────────────────────────── -->
    <template v-else-if="tab === 'svg'">
      <input
        v-model="search"
        type="text"
        placeholder="Suchen …"
        class="w-full rounded px-2 py-1 text-sm focus:outline-none"
        :class="inputCls"
      />
      <p v-if="filteredSvg.length === 0" class="text-xs py-2 text-center" :class="props.dark ? 'text-gray-500' : 'text-gray-400 dark:text-gray-500'">
        {{ search ? 'Keine Treffer' : 'Keine Icons importiert' }}
      </p>
      <div v-else class="flex flex-wrap gap-1 max-h-40 overflow-y-auto">
        <button
          v-for="name in filteredSvg"
          :key="name"
          type="button"
          class="flex flex-col items-center gap-0.5"
          :class="[svgBtnBase, modelValue === `svg:${name}` ? svgBtnActiveCls : '']"
          :title="name"
          @click="selectSvg(name)"
        >
          <span class="w-6 h-6 flex items-center justify-center text-lg">
            <VisuIcon :icon="`svg:${name}`" />
          </span>
          <span class="text-[9px] truncate max-w-[48px]" :class="props.dark ? 'text-gray-400' : 'text-gray-500 dark:text-gray-400'">
            {{ name }}
          </span>
        </button>
      </div>
    </template>
  </div>
</template>
