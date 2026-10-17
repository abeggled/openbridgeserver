<template>
  <Modal
    :model-value="modelValue"
    :title="$t('updates.releaseNotes.title')"
    max-width="3xl"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div class="flex flex-col gap-4" data-testid="release-notes-modal">
      <p class="text-xs text-slate-500">
        {{ $t('updates.releaseNotes.range', { installed: status?.installed_version ?? '—', latest: status?.latest_version ?? '—' }) }}
      </p>
      <div v-if="store.notesLoading" class="flex justify-center py-6"><Spinner /></div>
      <p v-else-if="store.notesError" class="text-sm text-red-500" data-testid="release-notes-error">{{ $t('updates.releaseNotes.loadError') }}</p>
      <p v-else-if="!store.releaseNotes.length" class="text-sm text-slate-500" data-testid="release-notes-empty">{{ $t('updates.releaseNotes.empty') }}</p>
      <template v-else>
        <section
          v-for="note in store.releaseNotes"
          :key="note.version"
          class="flex flex-col gap-2 border-t border-slate-200 dark:border-slate-700/60 pt-3 first:border-t-0 first:pt-0"
          :data-testid="`release-note-${note.version}`"
        >
          <div class="flex items-baseline justify-between gap-3 flex-wrap">
            <h4 class="font-semibold text-slate-800 dark:text-slate-100">{{ note.name }}</h4>
            <div class="flex items-center gap-3 text-xs text-slate-500">
              <span v-if="note.published_at">{{ fmtDateTime(note.published_at) }}</span>
              <a v-if="safeHref(note.url)" :href="safeHref(note.url)" target="_blank" rel="noopener noreferrer" class="text-blue-500 hover:underline">
                {{ $t('updates.releaseNotes.openOnGithub') }}
              </a>
            </div>
          </div>
          <SafeMarkdown :source="note.body" />
        </section>
      </template>
    </div>
  </Modal>
</template>

<script setup>
import { computed, watch } from 'vue'
import Modal from '@/components/ui/Modal.vue'
import Spinner from '@/components/ui/Spinner.vue'
import SafeMarkdown from '@/components/ui/SafeMarkdown.vue'
import { useTz } from '@/composables/useTz'
import { useUpdatesStore } from '@/stores/updates'
import { safeHref } from '@/utils/safeMarkdown'

const props = defineProps({ modelValue: Boolean })
defineEmits(['update:modelValue'])

const store = useUpdatesStore()
const { fmtDateTime } = useTz()
const status = computed(() => store.status)

watch(() => props.modelValue, (open) => {
  if (open) store.fetchReleaseNotes()
}, { immediate: true })
</script>
