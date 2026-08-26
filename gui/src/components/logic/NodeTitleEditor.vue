<!--
  Inline block-name editor for the logic sheet (issue #1157).

  Several blocks of the same type carry the same generated title ("OBJEKT
  LESEN"), so a sheet with more than one of them is unreadable. A double-click
  on the title turns it into a text field: Enter or losing focus commits,
  Escape aborts, and an empty value clears the custom name so the block falls
  back to `fallback` — the default title of its block type.

  The custom name lives in the block's `data.label` and is written by the host
  card; the generated node id stays untouched, so edges and references are
  unaffected.
-->
<template>
  <!-- The field sits on a canvas whose shortcuts (VueFlow's delete key,
       LogicView's copy/paste) listen on the document. Both skip events coming
       from an input today; `@keydown.stop` keeps that true for any handler
       added later, so typing a block name can never delete or paste a block. -->
  <input
    v-if="editing"
    ref="inputRef"
    v-model="draft"
    type="text"
    maxlength="80"
    class="nte-input nodrag"
    :placeholder="fallback"
    :aria-label="$t('logic.blockName.label')"
    data-testid="node-title-input"
    @keydown.enter.prevent="commit"
    @keydown.esc.prevent.stop="cancel"
    @keydown.stop
    @blur="commit"
    @mousedown.stop
    @dblclick.stop
  />
  <span
    v-else
    :class="titleClass"
    :title="editable ? $t('logic.blockName.editTitle', { name: label }) : label"
    data-testid="node-title"
    @dblclick.stop.prevent="startEdit"
  >{{ label }}</span>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'

const props = defineProps({
  // Stored custom name — empty means "use the block type's default title".
  value:      { type: String, default: '' },
  // Default title of the block type, shown when no custom name is set.
  fallback:   { type: String, required: true },
  // Class list of the host card's title element, so each card keeps its own
  // typography (a scoped parent rule applies to this component's root).
  titleClass: { type: [String, Array, Object], default: '' },
  editable:   { type: Boolean, default: true },
})

const emit = defineEmits(['rename'])

const custom = computed(() => props.value.trim())
const label = computed(() => custom.value || props.fallback)

const editing = ref(false)
const draft = ref('')
const inputRef = ref(null)

function startEdit() {
  if (!props.editable) return
  draft.value = custom.value
  editing.value = true
  nextTick(() => {
    inputRef.value?.focus()
    inputRef.value?.select()
  })
}

function commit() {
  // Escape closes the field first; the blur that follows must not re-commit
  // the draft it just discarded.
  if (!editing.value) return
  editing.value = false
  const next = draft.value.trim()
  if (next !== custom.value) emit('rename', next)
}

function cancel() {
  editing.value = false
  draft.value = custom.value
}
</script>

<style scoped>
.nte-input {
  /* Zero basis: the field fills the header row's leftover space and never
     grows past it, so the card's delete button keeps its full width. */
  flex: 1 1 0;
  min-width: 0;
  padding: 0;
  border: none;
  border-bottom: 1px solid var(--node-accent-hover, #38bdf8);
  border-radius: 0;
  background: transparent;
  color: var(--node-title-color);
  font: inherit;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .04em;
  outline: none;
}
</style>
