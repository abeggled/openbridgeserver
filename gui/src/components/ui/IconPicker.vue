<script setup>
/**
 * IconPicker for the gui app.
 * v-model: string — either an emoji ("🔗") or an SVG reference ("svg:{name}")
 */
import { ref, computed, watch, onMounted } from 'vue'
import { useIcons } from '@/composables/useIcons'
import VisuIcon from '@/components/ui/VisuIcon.vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const QUICK_ICONS = [
  '🏠','🏡','📁','📄','⚡','💡','🌡️','🔒','🚿','🛁','🛋️','🍳','🛏️',
  '🪟','🚪','🌿','🔆','🔅','💧','🌬️','🎵','📹','🔔','🌞','🌙','🎛️',
  '🎚️','🔌','🌐','⚙️','🔗','🏊','🌳','🚗','🔑','📊','🔋','🏭','☀️',
]

const { iconNames, loadList, isSvgIcon } = useIcons()
const tab    = ref('emoji')
const search = ref('')

watch(
  () => props.modelValue,
  (val) => { if (isSvgIcon(val)) tab.value = 'svg' },
  { immediate: true },
)

const filteredSvg = computed(() => {
  const q = search.value.toLowerCase().trim()
  return q ? iconNames.value.filter(n => n.includes(q)) : iconNames.value
})

function selectEmoji(ic) { emit('update:modelValue', ic) }
function selectSvg(name)  { emit('update:modelValue', `svg:${name}`) }
function onCustomInput(e) { if (e.target.value) emit('update:modelValue', e.target.value) }

onMounted(loadList)
</script>

<template>
  <div class="space-y-2">
    <!-- Tabs -->
    <div class="flex gap-1">
      <button
        type="button"
        :class="['rounded px-2 py-0.5 text-xs font-medium transition-colors',
          tab === 'emoji'
            ? 'bg-blue-500/10 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400'
            : 'text-slate-400 hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-300']"
        @click="tab = 'emoji'"
      >Emoji</button>
      <button
        v-if="iconNames.length"
        type="button"
        :class="['rounded px-2 py-0.5 text-xs font-medium transition-colors',
          tab === 'svg'
            ? 'bg-blue-500/10 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400'
            : 'text-slate-400 hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-300']"
        @click="tab = 'svg'"
      >Icons ({{ iconNames.length }})</button>
    </div>

    <!-- Emoji tab -->
    <template v-if="tab === 'emoji'">
      <input
        :value="isSvgIcon(modelValue) ? '' : (modelValue ?? '')"
        type="text"
        maxlength="4"
        placeholder="Emoji"
        class="w-14 text-center rounded px-2 py-1 text-lg focus:outline-none input"
        @input="onCustomInput"
      />
      <div class="flex flex-wrap gap-1">
        <button
          v-for="ic in QUICK_ICONS"
          :key="ic"
          type="button"
          class="w-7 h-7 text-base flex items-center justify-center rounded transition-colors hover:bg-slate-100 dark:hover:bg-slate-700"
          :class="modelValue === ic ? 'ring-2 ring-blue-500 bg-blue-50 dark:bg-blue-500/20' : ''"
          :title="ic"
          @click="selectEmoji(ic)"
        >{{ ic }}</button>
      </div>
    </template>

    <!-- SVG icons tab -->
    <template v-else-if="tab === 'svg'">
      <input
        v-model="search"
        type="text"
        placeholder="Suchen …"
        class="input w-full text-sm"
      />
      <p v-if="filteredSvg.length === 0" class="text-xs py-2 text-center text-slate-400 dark:text-slate-500">
        {{ search ? 'Keine Treffer' : 'Keine Icons importiert' }}
      </p>
      <div v-else class="flex flex-wrap gap-1 max-h-40 overflow-y-auto">
        <button
          v-for="name in filteredSvg"
          :key="name"
          type="button"
          class="flex flex-col items-center gap-0.5 border rounded p-1 transition-colors"
          :class="modelValue === `svg:${name}`
            ? 'border-blue-500 bg-blue-50 dark:bg-blue-500/20'
            : 'border-slate-200 dark:border-slate-700 hover:border-slate-400 dark:hover:border-slate-500'"
          :title="name"
          @click="selectSvg(name)"
        >
          <span class="w-6 h-6 flex items-center justify-center text-lg">
            <VisuIcon :icon="`svg:${name}`" />
          </span>
          <span class="text-[9px] truncate max-w-[48px] text-slate-500 dark:text-slate-400">{{ name }}</span>
        </button>
      </div>
    </template>
  </div>
</template>
