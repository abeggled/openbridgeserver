import { computed, inject, provide } from 'vue'

// Whether the logic canvas currently accepts edits (issue #1333). The editor
// makes it read-only while a sheet loads or is being swapped; the delete
// button on each block card must respect that like the canvas does.
export const CANVAS_EDITABLE_KEY = Symbol('logicCanvasEditable')

export function provideCanvasEditable(editable) {
  provide(CANVAS_EDITABLE_KEY, editable)
}

// Outside the editor (previews, isolated tests) a block counts as editable.
export function useCanvasEditable() {
  return inject(CANVAS_EDITABLE_KEY, computed(() => true))
}
