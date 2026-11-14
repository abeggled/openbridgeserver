<script setup lang="ts">
import { reactive, watch } from 'vue'
import IconPicker from '@/components/IconPicker.vue'
import PagePicker from '@/components/PagePicker.vue'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive({
  label:               (props.modelValue.label               as string)  ?? '',
  icon:                (props.modelValue.icon                as string)  ?? '🔗',
  target_node_id:      (props.modelValue.target_node_id      as string)  ?? '',
  history_back:        (props.modelValue.history_back        as boolean) ?? false,
  show_arrow:          (props.modelValue.show_arrow          as boolean) ?? true,
  show_icon:           (props.modelValue.show_icon           as boolean) ?? true,
  preserve_icon_color: (props.modelValue.preserve_icon_color as boolean) ?? false,
  label_size:          (props.modelValue.label_size          as string)  ?? 'sm',
  active_indicator:    (props.modelValue.active_indicator    as string)  ?? 'none',
  icon_size:           (props.modelValue.icon_size           as number)  ?? 0,
})

// Sync bei Widget-Wechsel
watch(() => props.modelValue, (v) => {
  cfg.label               = (v.label               as string)  ?? ''
  cfg.icon                = (v.icon                as string)  ?? '🔗'
  cfg.target_node_id      = (v.target_node_id      as string)  ?? ''
  cfg.history_back        = (v.history_back        as boolean) ?? false
  cfg.show_arrow          = (v.show_arrow          as boolean) ?? true
  cfg.show_icon           = (v.show_icon           as boolean) ?? true
  cfg.preserve_icon_color = (v.preserve_icon_color as boolean) ?? false
  cfg.label_size          = (v.label_size          as string)  ?? 'sm'
  cfg.active_indicator    = (v.active_indicator    as string)  ?? 'none'
  cfg.icon_size           = (v.icon_size           as number)  ?? 0
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-4">

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ $t('widgets.common.label') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        :placeholder="$t('widgets.link.labelPlaceholder')"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Icon-Auswahl -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ $t('widgets.stufenschalter.icon') }}</label>
      <IconPicker v-model="cfg.icon" />
    </div>

    <!-- Icon anzeigen -->
    <label class="flex items-center gap-2 cursor-pointer select-none">
      <input type="checkbox" v-model="cfg.show_icon" class="rounded" />
      <span class="text-xs text-gray-500 dark:text-gray-400">{{ $t('widgets.link.showIcon') }}</span>
    </label>

    <!-- Icon-Grösse -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ $t('widgets.link.iconSize') }}</label>
      <input
        v-model.number="cfg.icon_size"
        type="number"
        min="0"
        step="1"
        data-testid="link-icon-size"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
      <p class="text-xs text-gray-400 dark:text-gray-500 mt-1">{{ $t('widgets.link.iconSizeHint') }}</p>
    </div>

    <!-- SVG-Farbe erhalten -->
    <label class="flex items-center gap-2 cursor-pointer select-none">
      <input type="checkbox" v-model="cfg.preserve_icon_color" class="rounded" />
      <span class="text-xs text-gray-500 dark:text-gray-400">{{ $t('widgets.link.preserveIconColor') }}</span>
    </label>

    <!-- Schriftgrösse Label -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ $t('widgets.common.fontSize') }}</label>
      <select
        v-model="cfg.label_size"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="xs">{{ $t('widgets.common.fontSizeXs') }}</option>
        <option value="sm">{{ $t('widgets.common.fontSizeSm') }}</option>
        <option value="md">{{ $t('widgets.common.fontSizeMd') }}</option>
        <option value="lg">{{ $t('widgets.common.fontSizeLg') }}</option>
        <option value="xl">{{ $t('widgets.common.fontSizeXl') }}</option>
      </select>
    </div>

    <!-- Aktive Seite markieren -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ $t('widgets.link.activeIndicator') }}</label>
      <select
        v-model="cfg.active_indicator"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="none">{{ $t('widgets.link.activeIndicatorNone') }}</option>
        <option value="dot">{{ $t('widgets.link.activeIndicatorDot') }}</option>
        <option value="bar">{{ $t('widgets.link.activeIndicatorLine') }}</option>
        <option value="border">{{ $t('widgets.link.activeIndicatorBorder') }}</option>
      </select>
    </div>

    <!-- Navigationspfeil anzeigen -->
    <label class="flex items-center gap-2 cursor-pointer select-none">
      <input type="checkbox" v-model="cfg.show_arrow" class="rounded" />
      <span class="text-xs text-gray-500 dark:text-gray-400">{{ $t('widgets.link.showArrow') }}</span>
    </label>

    <!-- Zurück zur vorherigen Seite (Browser-Verlauf) -->
    <label class="flex items-center gap-2 cursor-pointer select-none">
      <input type="checkbox" v-model="cfg.history_back" class="rounded" data-testid="link-config-history-back" />
      <span class="text-xs text-gray-500 dark:text-gray-400">{{ $t('widgets.link.historyBack') }}</span>
    </label>

    <!-- Ziel-Seite (suchbarer Picker) -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ $t('widgets.link.targetPage') }}</label>
      <PagePicker v-model="cfg.target_node_id" />
    </div>

  </div>
</template>
