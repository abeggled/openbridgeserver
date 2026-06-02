<script setup lang="ts">
import { reactive, watch } from 'vue'

const props = defineProps<{
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, unknown>): void
}>()

const cfg = reactive({
  content:  (props.modelValue.content  as string) ?? '',
  align:    (props.modelValue.align    as string) ?? 'left',
  fontSize: (props.modelValue.fontSize as string) ?? 'base',
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-3">
    <!-- Inhalt -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">
        {{ $t('widgets.text.content') }}
        <span class="text-gray-600 font-normal ml-1">{{ $t('widgets.text.markdownSupported') }}</span>
      </label>
      <textarea
        v-model="cfg.content"
        rows="8"
        placeholder="Text eingeben…&#10;&#10;**Fett**, *kursiv*, [Link](https://…)&#10;&#10;- Aufzählung&#10;1. Nummerierung&#10;&#10;`Code`"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500 resize-y"
      />
    </div>

    <!-- Schriftgrösse + Ausrichtung -->
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.text.fontSize') }}</label>
        <select
          v-model="cfg.fontSize"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="xs">{{ $t('widgets.text.sizeXs') }}</option>
          <option value="sm">{{ $t('widgets.text.sizeSm') }}</option>
          <option value="base">{{ $t('widgets.text.sizeBase') }}</option>
          <option value="lg">{{ $t('widgets.text.sizeLg') }}</option>
          <option value="xl">{{ $t('widgets.text.sizeXl') }}</option>
          <option value="2xl">{{ $t('widgets.text.size2xl') }}</option>
        </select>
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.text.alignment') }}</label>
        <select
          v-model="cfg.align"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="left">{{ $t('widgets.text.alignLeft') }}</option>
          <option value="center">{{ $t('widgets.text.alignCenter') }}</option>
          <option value="right">{{ $t('widgets.text.alignRight') }}</option>
        </select>
      </div>
    </div>

    <!-- Markdown-Hilfe -->
    <div class="text-xs text-gray-600 border border-gray-700 rounded p-2 space-y-0.5">
      <p class="text-gray-500 font-semibold mb-1">{{ $t('widgets.text.markdownRef') }}</p>
      <p><code class="text-gray-400"># Überschrift 1</code>  <code class="text-gray-400">## Überschrift 2</code></p>
      <p><code class="text-gray-400">**fett**</code>  <code class="text-gray-400">*kursiv*</code></p>
      <p><code class="text-gray-400">[Link](https://…)</code></p>
      <p><code class="text-gray-400">- Aufzählung</code>  <code class="text-gray-400">1. Nummerierung</code></p>
      <p><code class="text-gray-400">`inline code`</code>  oder Codeblock mit ``` ```</p>
    </div>
  </div>
</template>
