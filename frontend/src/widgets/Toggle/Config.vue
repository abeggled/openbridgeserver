<script setup lang="ts">
import { reactive, watch, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import IconPicker from '@/components/IconPicker.vue'

type DisplayMode = 'switch' | 'icon_only' | 'icon_text'

interface StateRule {
  icon: string
  color: string
  text: string
}

interface Cfg {
  label:      string
  mode:       DisplayMode
  label_size: string
  on:         StateRule
  off:        StateRule
}



const { t } = useI18n()

const MODES = computed(() => [
  { value: 'switch'    as DisplayMode, label: t('widgets.toggle.modeSwitch') },
  { value: 'icon_only' as DisplayMode, label: t('widgets.toggle.modeIconOnly') },
  { value: 'icon_text' as DisplayMode, label: t('widgets.toggle.modeIconText') },
])

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

function parseRule(raw: unknown, defaults: StateRule): StateRule {
  const r = raw as Partial<StateRule> | undefined
  return {
    icon:  r?.icon  ?? defaults.icon,
    color: r?.color ?? defaults.color,
    text:  r?.text  ?? defaults.text,
  }
}

const cfg = reactive<Cfg>({
  label:      (props.modelValue.label      as string)      ?? '',
  mode:       (props.modelValue.mode       as DisplayMode) ?? 'switch',
  label_size: (props.modelValue.label_size as string)      ?? 'xs',
  on:  parseRule(props.modelValue.on,  { icon: '', color: '#3b82f6', text: t('common.on') }),
  off: parseRule(props.modelValue.off, { icon: '', color: '#6b7280', text: t('common.off') }),
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-4 text-sm">

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.common.label') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        :placeholder="$t('widgets.toggle.labelPlaceholder')"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Schriftgrösse -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.common.fontSize') }}</label>
      <select
        v-model="cfg.label_size"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="xs">{{ $t('widgets.common.fontSizeXs') }} – Standard (text-xs)</option>
        <option value="sm">{{ $t('widgets.common.fontSizeSm') }} (text-sm)</option>
        <option value="md">{{ $t('widgets.common.fontSizeMd') }} (text-base)</option>
        <option value="lg">{{ $t('widgets.common.fontSizeLg') }} (text-lg)</option>
        <option value="xl">{{ $t('widgets.common.fontSizeXl') }} (text-xl)</option>
      </select>
    </div>

    <!-- Modus -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('common.mode') }}</label>
      <div class="flex gap-1">
        <button
          v-for="m in MODES"
          :key="m.value"
          type="button"
          :class="[
            'flex-1 py-1.5 text-xs rounded border',
            cfg.mode === m.value
              ? 'border-blue-500 bg-blue-500/20 text-blue-300'
              : 'border-gray-700 text-gray-400 hover:border-gray-500',
          ]"
          @click="cfg.mode = m.value"
        >{{ m.label }}</button>
      </div>
    </div>

    <!-- ── Zustandsregeln ─────────────────────────────────────────────────── -->
    <div class="space-y-2">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">{{ $t('widgets.toggle.states') }}</p>

      <!-- EIN (true) -->
      <div class="border border-gray-700 rounded p-2 space-y-2">
        <p class="text-xs font-semibold text-blue-400">{{ $t('widgets.toggle.stateOn') }}</p>

        <!-- Icon + Farbe -->
        <div class="flex gap-2 items-center">
          <span class="text-xs text-gray-500 w-8 shrink-0">{{ $t('widgets.toggle.iconLabel') }}</span>
          <IconPicker v-model="cfg.on.icon" :dark="true" />
          <input
            v-model="cfg.on.color"
            type="color"
            class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
            :title="$t('widgets.toggle.colorTitle')"
          />
        </div>

        <!-- Text (nicht im Nur-Icon-Modus) -->
        <div v-if="cfg.mode !== 'icon_only'" class="flex gap-2 items-center">
          <span class="text-xs text-gray-500 w-8 shrink-0">{{ $t('widgets.toggle.textLabel') }}</span>
          <input
            v-model="cfg.on.text"
            type="text"
            :placeholder="$t('common.on')"
            class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      <!-- AUS (false / default) -->
      <div class="border border-gray-700 rounded p-2 space-y-2">
        <p class="text-xs font-semibold text-gray-400">{{ $t('widgets.toggle.stateOff') }}</p>

        <!-- Icon + Farbe -->
        <div class="flex gap-2 items-center">
          <span class="text-xs text-gray-500 w-8 shrink-0">{{ $t('widgets.toggle.iconLabel') }}</span>
          <IconPicker v-model="cfg.off.icon" :dark="true" />
          <input
            v-model="cfg.off.color"
            type="color"
            class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
            :title="$t('widgets.toggle.colorTitle')"
          />
        </div>

        <!-- Text (nicht im Nur-Icon-Modus) -->
        <div v-if="cfg.mode !== 'icon_only'" class="flex gap-2 items-center">
          <span class="text-xs text-gray-500 w-8 shrink-0">{{ $t('widgets.toggle.textLabel') }}</span>
          <input
            v-model="cfg.off.text"
            type="text"
            :placeholder="$t('common.off')"
            class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
    </div>

  </div>
</template>
