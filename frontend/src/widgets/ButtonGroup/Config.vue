<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import IconPicker from '@/components/IconPicker.vue'

interface ButtonConfig {
  id: string
  label: string
  icon: string
  color: string
  value: string
  resetEnabled: boolean
  resetValue: string
  resetDelayMs: number
}

interface Cfg {
  label: string
  columns: number
  showLabel: boolean
  buttons: ButtonConfig[]
}

const MIN_BUTTONS = 1
const MAX_BUTTONS = 12
const DEFAULT_BUTTON_LABEL = 'widgets.buttongroup.defaultButton'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()
const { t } = useI18n()

function newId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `button-${Date.now()}-${Math.round(Math.random() * 1000)}`
}

function parseBoolean(raw: unknown, fallback: boolean): boolean {
  if (typeof raw === 'boolean') return raw
  if (typeof raw === 'string') {
    const value = raw.trim().toLowerCase()
    if (value === 'true') return true
    if (value === 'false') return false
  }
  return fallback
}

function parseColumns(raw: unknown): number {
  const columns = Number(raw)
  return Number.isFinite(columns) ? Math.min(4, Math.max(1, Math.round(columns))) : 2
}

function parseDelay(raw: unknown): number {
  const delay = Number(raw)
  return Number.isFinite(delay) ? Math.max(0, Math.round(delay)) : 300
}

function defaultButtonLabel(index: number): string {
  return t('widgets.buttongroup.defaultButtonWithNumber', { number: index + 1 })
}

function normalizeButtonLabel(raw: unknown, index: number): string {
  if (typeof raw !== 'string') return DEFAULT_BUTTON_LABEL
  const label = raw.trim()
  if (!label || label === DEFAULT_BUTTON_LABEL || label === defaultButtonLabel(index)) return DEFAULT_BUTTON_LABEL
  return raw
}

function parseButtons(raw: unknown): ButtonConfig[] {
  const buttons = raw as Partial<ButtonConfig>[] | undefined
  if (!Array.isArray(buttons) || buttons.length < MIN_BUTTONS) {
    return [createButton()]
  }
  return buttons.slice(0, MAX_BUTTONS).map((button, index) => ({
    id: button.id || newId(),
    label: normalizeButtonLabel(button.label, index),
    icon: button.icon ?? '',
    color: button.color ?? '#3b82f6',
    value: String(button.value ?? 'true'),
    resetEnabled: parseBoolean(button.resetEnabled, false),
    resetValue: String(button.resetValue ?? 'false'),
    resetDelayMs: parseDelay(button.resetDelayMs),
  }))
}

function createButton(): ButtonConfig {
  return {
    id: newId(),
    label: DEFAULT_BUTTON_LABEL,
    icon: '',
    color: '#3b82f6',
    value: 'true',
    resetEnabled: false,
    resetValue: 'false',
    resetDelayMs: 300,
  }
}

const cfg = reactive<Cfg>({
  label: (props.modelValue.label as string) ?? '',
  columns: parseColumns(props.modelValue.columns),
  showLabel: props.modelValue.showLabel !== false,
  buttons: parseButtons(props.modelValue.buttons),
})

function serializedConfig(): Record<string, unknown> {
  return {
    label: cfg.label,
    columns: parseColumns(cfg.columns),
    showLabel: cfg.showLabel,
    buttons: cfg.buttons.map((button, index) => ({
      ...button,
      label: normalizeButtonLabel(button.label, index),
      resetDelayMs: parseDelay(button.resetDelayMs),
    })),
  }
}

watch(
  cfg,
  () => emit('update:modelValue', serializedConfig()),
  { deep: true },
)

function displayButtonLabel(button: ButtonConfig, index: number): string {
  return normalizeButtonLabel(button.label, index) === DEFAULT_BUTTON_LABEL
    ? defaultButtonLabel(index)
    : button.label
}

function updateButtonLabel(button: ButtonConfig, index: number, event: Event) {
  const value = (event.target as HTMLInputElement).value
  button.label = normalizeButtonLabel(value, index)
}

function updateColumns(event: Event) {
  cfg.columns = parseColumns((event.target as HTMLInputElement).value)
}

function addButton() {
  if (cfg.buttons.length >= MAX_BUTTONS) return
  cfg.buttons.push(createButton())
}

function removeButton(index: number) {
  if (cfg.buttons.length <= MIN_BUTTONS) return
  cfg.buttons.splice(index, 1)
}

function moveUp(index: number) {
  if (index === 0) return
  ;[cfg.buttons[index - 1], cfg.buttons[index]] = [cfg.buttons[index], cfg.buttons[index - 1]]
}

function moveDown(index: number) {
  if (index === cfg.buttons.length - 1) return
  ;[cfg.buttons[index + 1], cfg.buttons[index]] = [cfg.buttons[index], cfg.buttons[index + 1]]
}
</script>

<template>
  <div class="space-y-4 text-sm">
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.common.label') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        :placeholder="$t('widgets.buttongroup.labelPlaceholder')"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <div class="grid grid-cols-2 gap-2">
      <label class="block">
        <span class="block text-xs text-gray-400 mb-1">{{ $t('widgets.buttongroup.columns') }}</span>
        <input
          :value="cfg.columns"
          type="number"
          min="1"
          max="4"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          @input="updateColumns"
        />
      </label>
      <label class="flex items-end gap-2 text-xs text-gray-400 pb-2">
        <input v-model="cfg.showLabel" type="checkbox" />
        {{ $t('widgets.buttongroup.showTitle') }}
      </label>
    </div>

    <div>
      <div class="flex items-center justify-between mb-2">
        <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          {{ $t('widgets.buttongroup.buttons', { count: cfg.buttons.length, max: MAX_BUTTONS }) }}
        </p>
        <button
          type="button"
          :disabled="cfg.buttons.length >= MAX_BUTTONS"
          class="text-xs px-2 py-1 rounded border border-dashed border-gray-600 text-gray-400 hover:border-blue-500 hover:text-blue-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          @click="addButton"
        >{{ $t('widgets.buttongroup.addButton') }}</button>
      </div>

      <p class="text-xs text-gray-600 mb-2">
        {{ $t('widgets.buttongroup.writeHint') }}
      </p>

      <div class="space-y-2">
        <div
          v-for="(button, index) in cfg.buttons"
          :key="button.id"
          class="border border-gray-700 rounded p-2 space-y-2"
        >
          <div class="flex items-center gap-1">
            <span class="text-xs font-semibold text-gray-500 w-4 shrink-0">{{ index + 1 }}</span>
            <input
              :value="displayButtonLabel(button, index)"
              type="text"
              :placeholder="$t('widgets.common.label')"
              class="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
              @input="updateButtonLabel(button, index, $event)"
            />
            <button
              type="button"
              :disabled="index === 0"
              class="w-5 h-5 flex items-center justify-center rounded text-gray-500 hover:text-gray-300 disabled:opacity-20 text-xs"
              :title="$t('widgets.buttongroup.moveUp')"
              @click="moveUp(index)"
            >▲</button>
            <button
              type="button"
              :disabled="index === cfg.buttons.length - 1"
              class="w-5 h-5 flex items-center justify-center rounded text-gray-500 hover:text-gray-300 disabled:opacity-20 text-xs"
              :title="$t('widgets.buttongroup.moveDown')"
              @click="moveDown(index)"
            >▼</button>
            <button
              type="button"
              :disabled="cfg.buttons.length <= MIN_BUTTONS"
              class="w-5 h-5 flex items-center justify-center rounded text-gray-500 hover:text-red-400 disabled:opacity-20 text-xs"
              :title="$t('common.delete')"
              @click="removeButton(index)"
            >✕</button>
          </div>

          <div class="flex gap-2 items-center">
            <span class="text-xs text-gray-500 w-10 shrink-0">{{ $t('widgets.buttongroup.icon') }}</span>
            <IconPicker v-model="button.icon" :dark="true" />
            <input
              v-model="button.color"
              type="color"
              class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
              :title="$t('widgets.buttongroup.color')"
            />
          </div>

          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="block text-xs text-gray-500 mb-1">{{ $t('widgets.buttongroup.value') }}</span>
              <input
                v-model="button.value"
                type="text"
                :placeholder="$t('widgets.buttongroup.valuePlaceholder')"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
              />
            </label>
            <label class="flex items-end gap-2 text-xs text-gray-400 pb-1">
              <input v-model="button.resetEnabled" type="checkbox" />
              {{ $t('widgets.buttongroup.reset') }}
            </label>
          </div>

          <div v-if="button.resetEnabled" class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="block text-xs text-gray-500 mb-1">{{ $t('widgets.buttongroup.resetValue') }}</span>
              <input
                v-model="button.resetValue"
                type="text"
                placeholder="false"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
              />
            </label>
            <label class="block">
              <span class="block text-xs text-gray-500 mb-1">{{ $t('widgets.buttongroup.resetDelay') }}</span>
              <input
                v-model.number="button.resetDelayMs"
                type="number"
                min="0"
                step="50"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
              />
            </label>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
