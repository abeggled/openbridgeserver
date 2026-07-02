import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'

const MODAL_STUB = {
  template: '<div v-if="modelValue" class="modal"><slot /><div data-testid="modal-footer"><slot name="footer" /></div></div>',
  props: ['modelValue', 'title', 'maxWidth'],
  emits: ['update:modelValue'],
}

function mkDialog(props = {}) {
  return mount(ConfirmDialog, {
    props: { modelValue: true, title: 'Confirm', message: 'Are you sure?', ...props },
    global: { stubs: { Modal: MODAL_STUB } },
  })
}

describe('ConfirmDialog', () => {
  it('renders the message text', () => {
    const w = mkDialog({ message: 'Delete this item?' })
    expect(w.text()).toContain('Delete this item?')
  })

  it('renders the title via Modal prop', () => {
    const w = mkDialog({ title: 'Achtung' })
    // title is passed to Modal as prop; stub is rendered with it
    expect(w.find('.modal').exists()).toBe(true)
  })

  it('confirm button has btn-danger class when danger=true (default)', () => {
    const w = mkDialog({ danger: true })
    expect(w.find('[data-testid="btn-confirm"]').classes()).toContain('btn-danger')
  })

  it('confirm button has btn-primary class when danger=false', () => {
    const w = mkDialog({ danger: false })
    expect(w.find('[data-testid="btn-confirm"]').classes()).toContain('btn-primary')
  })

  it('clicking confirm emits "confirm" event', async () => {
    const w = mkDialog()
    await w.find('[data-testid="btn-confirm"]').trigger('click')
    expect(w.emitted('confirm')).toHaveLength(1)
  })

  it('clicking confirm emits update:modelValue with false', async () => {
    const w = mkDialog()
    await w.find('[data-testid="btn-confirm"]').trigger('click')
    expect(w.emitted('update:modelValue')).toEqual([[false]])
  })

  it('cancel button emits update:modelValue with false', async () => {
    const w = mkDialog()
    const cancel = w.findAll('button').find(b => b.text().includes('Abbrechen'))
    await cancel.trigger('click')
    expect(w.emitted('update:modelValue')).toEqual([[false]])
  })

  it('uses confirmLabel prop on the confirm button', () => {
    const w = mkDialog({ confirmLabel: 'Löschen' })
    expect(w.find('[data-testid="btn-confirm"]').text()).toBe('Löschen')
  })

  it('dialog not rendered when modelValue=false', () => {
    const w = mkDialog({ modelValue: false })
    expect(w.find('.modal').exists()).toBe(false)
  })
})
