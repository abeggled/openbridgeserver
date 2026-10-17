/**
 * Issue #1157 — inline block rename on the logic sheet.
 *
 * Double-clicking the block title turns it into a text field; Enter or losing
 * focus commits, Escape aborts, and clearing the field falls back to the block
 * type's default title.
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import NodeTitleEditor from '@/components/logic/NodeTitleEditor.vue'

function mountEditor(props = {}) {
  return mount(NodeTitleEditor, {
    props: { fallback: 'OBJEKT LESEN', ...props },
    attachTo: document.body,
  })
}

describe('NodeTitleEditor', () => {
  it('shows the block type default title when no name is set', () => {
    const w = mountEditor()
    expect(w.get('[data-testid="node-title"]').text()).toBe('OBJEKT LESEN')
  })

  it('shows the custom name instead of the default title', () => {
    const w = mountEditor({ value: 'Wohnzimmer Temperatur' })
    expect(w.get('[data-testid="node-title"]').text()).toBe('Wohnzimmer Temperatur')
  })

  it('ignores a whitespace-only name and falls back to the default title', () => {
    const w = mountEditor({ value: '   ' })
    expect(w.get('[data-testid="node-title"]').text()).toBe('OBJEKT LESEN')
  })

  it('applies the host card title classes to the title element', () => {
    const w = mountEditor({ value: 'Küche', titleClass: ['gn-title', 'gn-title--custom'] })
    expect(w.get('[data-testid="node-title"]').classes()).toEqual(
      expect.arrayContaining(['gn-title', 'gn-title--custom']),
    )
  })

  it('opens an input on double-click, prefilled with the current name', async () => {
    const w = mountEditor({ value: 'Küche' })
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    const input = w.get('[data-testid="node-title-input"]')
    expect(input.element.value).toBe('Küche')
    expect(input.attributes('placeholder')).toBe('OBJEKT LESEN')
  })

  it('prefills an empty draft when only the default title is shown', async () => {
    const w = mountEditor()
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    expect(w.get('[data-testid="node-title-input"]').element.value).toBe('')
  })

  it('does not open the input when editing is disabled', async () => {
    const w = mountEditor({ editable: false })
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    expect(w.find('[data-testid="node-title-input"]').exists()).toBe(false)
  })

  it('commits a new name on Enter', async () => {
    const w = mountEditor()
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    await w.get('[data-testid="node-title-input"]').setValue('Bad Fenster')
    await w.get('[data-testid="node-title-input"]').trigger('keydown.enter')
    expect(w.emitted('rename')).toEqual([['Bad Fenster']])
    expect(w.find('[data-testid="node-title-input"]').exists()).toBe(false)
  })

  it('commits a new name on blur', async () => {
    const w = mountEditor()
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    await w.get('[data-testid="node-title-input"]').setValue('Bad Fenster')
    await w.get('[data-testid="node-title-input"]').trigger('blur')
    expect(w.emitted('rename')).toEqual([['Bad Fenster']])
  })

  it('trims the committed name', async () => {
    const w = mountEditor()
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    await w.get('[data-testid="node-title-input"]').setValue('  Bad Fenster  ')
    await w.get('[data-testid="node-title-input"]').trigger('keydown.enter')
    expect(w.emitted('rename')).toEqual([['Bad Fenster']])
  })

  it('emits an empty name when the field is cleared, restoring the default title', async () => {
    const w = mountEditor({ value: 'Küche' })
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    await w.get('[data-testid="node-title-input"]').setValue('   ')
    await w.get('[data-testid="node-title-input"]').trigger('keydown.enter')
    expect(w.emitted('rename')).toEqual([['']])
  })

  it('does not emit when the name is unchanged', async () => {
    const w = mountEditor({ value: 'Küche' })
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    await w.get('[data-testid="node-title-input"]').trigger('keydown.enter')
    expect(w.emitted('rename')).toBeUndefined()
  })

  it('discards the draft on Escape', async () => {
    const w = mountEditor({ value: 'Küche' })
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    await w.get('[data-testid="node-title-input"]').setValue('Verworfen')
    await w.get('[data-testid="node-title-input"]').trigger('keydown.esc')
    expect(w.emitted('rename')).toBeUndefined()
    expect(w.get('[data-testid="node-title"]').text()).toBe('Küche')
  })

  it('ignores the blur that follows an Escape', async () => {
    const w = mountEditor({ value: 'Küche' })
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    const input = w.get('[data-testid="node-title-input"]')
    await input.setValue('Verworfen')
    await input.trigger('keydown.esc')
    await input.trigger('blur')
    expect(w.emitted('rename')).toBeUndefined()
  })

  it('reopens the field with the stored name after a cancelled edit', async () => {
    const w = mountEditor({ value: 'Küche' })
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    await w.get('[data-testid="node-title-input"]').setValue('Verworfen')
    await w.get('[data-testid="node-title-input"]').trigger('keydown.esc')
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    expect(w.get('[data-testid="node-title-input"]').element.value).toBe('Küche')
  })

  it('keeps the field out of the VueFlow drag handling', async () => {
    const w = mountEditor()
    await w.get('[data-testid="node-title"]').trigger('dblclick')
    expect(w.get('[data-testid="node-title-input"]').classes()).toContain('nodrag')
  })

  it('hints at the rename gesture in the title tooltip', () => {
    const w = mountEditor({ value: 'Küche' })
    expect(w.get('[data-testid="node-title"]').attributes('title')).toContain('Küche')
    expect(w.get('[data-testid="node-title"]').attributes('title')).toContain('Doppelklick')
  })

  it('falls back to the plain name in the tooltip when editing is disabled', () => {
    const w = mountEditor({ value: 'Küche', editable: false })
    expect(w.get('[data-testid="node-title"]').attributes('title')).toBe('Küche')
  })
})
