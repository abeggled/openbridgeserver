/**
 * Tests for the Modal.vue component, focusing on the new softBackdrop prop
 * (issue #435) which introduces a non-blocking, low-opacity backdrop variant
 * intended for soft-modal patterns where the underlying page should remain
 * interactive.
 *
 * Default behaviour (softBackdrop=false) must remain backwards compatible:
 *   - Clicking outside the panel closes the modal
 *   - ESC closes the modal
 *   - X-button closes the modal
 *   - Backdrop uses bg-black/60 with backdrop-blur-sm
 *
 * softBackdrop=true must:
 *   - Use bg-slate-900/5 dark:bg-black/20 without backdrop-blur-sm
 *   - Have pointer-events:none on the backdrop wrapper so clicks pass through
 *   - Click outside the panel does NOT close the modal
 *   - ESC still closes the modal
 *   - X-button still closes the modal
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import Modal from '@/components/ui/Modal.vue'

function mountModal(props = {}) {
  return mount(Modal, {
    props: {
      modelValue: true,
      title: 'Test Modal',
      ...props,
    },
    slots: {
      default: '<div data-testid="modal-body">body</div>',
    },
    attachTo: document.body,
  })
}

describe('Modal — default backdrop behaviour', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('renders the panel and a backdrop element when modelValue=true', () => {
    const wrapper = mountModal()
    const teleported = document.body.querySelector('[data-testid="modal-body"]')
    expect(teleported).toBeTruthy()
    const backdrop = document.querySelector('.bg-black\\/60')
    expect(backdrop).toBeTruthy()
  })

  it('uses bg-black/60 with backdrop-blur-sm by default', () => {
    mountModal()
    const backdrop = document.querySelector('.bg-black\\/60')
    expect(backdrop).toBeTruthy()
    expect(backdrop.className).toContain('backdrop-blur-sm')
  })

  it('emits update:modelValue=false when clicking outside the panel (default)', async () => {
    const wrapper = mountModal()
    const outer = document.querySelector('.fixed.inset-0.z-50')
    expect(outer).toBeTruthy()
    // Simulate a mousedown directly on the outer wrapper (i.e. backdrop area)
    outer.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await nextTick()
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1][0]).toBe(false)
  })

  it('emits update:modelValue=false when clicking the X button', async () => {
    const wrapper = mountModal()
    const closeBtn = document.querySelector('.btn-icon')
    expect(closeBtn).toBeTruthy()
    closeBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1][0]).toBe(false)
  })

  it('emits update:modelValue=false when ESC is pressed', async () => {
    const wrapper = mountModal()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1][0]).toBe(false)
  })

  it('does NOT close on ESC while focus is in a text field', async () => {
    // ESC inside an input/textarea/select belongs to the field itself
    // (closing a dropdown, aborting IME composition, etc.) — the modal
    // must stay open. After the field is blurred the next ESC closes it.
    const wrapper = mountModal()
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    const events = wrapper.emitted('update:modelValue') ?? []
    expect(events).toEqual([])
  })

  it('does NOT close on ESC while focus is in a textarea or select', async () => {
    const wrapper = mountModal()
    const textarea = document.createElement('textarea')
    const select = document.createElement('select')
    document.body.appendChild(textarea)
    document.body.appendChild(select)
    textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    select.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    const events = wrapper.emitted('update:modelValue') ?? []
    expect(events).toEqual([])
  })
})

describe('Modal — softBackdrop=true', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('uses bg-slate-900/5 / dark:bg-black/20 and omits backdrop-blur-sm', () => {
    mountModal({ softBackdrop: true })
    // soft backdrop should be there
    const softBackdrop = document.querySelector('.bg-slate-900\\/5')
    expect(softBackdrop).toBeTruthy()
    expect(softBackdrop.className).toContain('dark:bg-black/20')
    expect(softBackdrop.className).not.toContain('backdrop-blur-sm')
    // hard backdrop should NOT be there
    const hardBackdrop = document.querySelector('.bg-black\\/60')
    expect(hardBackdrop).toBeFalsy()
  })

  it('marks the backdrop wrapper as pointer-events:none so clicks pass through', () => {
    mountModal({ softBackdrop: true })
    const softBackdrop = document.querySelector('.bg-slate-900\\/5')
    expect(softBackdrop).toBeTruthy()
    expect(softBackdrop.className).toContain('pointer-events-none')
  })

  it('does NOT close when clicking outside the panel (soft mode)', async () => {
    const wrapper = mountModal({ softBackdrop: true })
    const outer = document.querySelector('.fixed.inset-0.z-50')
    expect(outer).toBeTruthy()
    outer.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await nextTick()
    const events = wrapper.emitted('update:modelValue')
    // Either no event at all, or no falsy emission triggered by outside-click
    expect(events ?? []).toEqual([])
  })

  it('still closes via ESC key in soft mode', async () => {
    const wrapper = mountModal({ softBackdrop: true })
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1][0]).toBe(false)
  })

  it('still closes via X button in soft mode', async () => {
    const wrapper = mountModal({ softBackdrop: true })
    const closeBtn = document.querySelector('.btn-icon')
    expect(closeBtn).toBeTruthy()
    closeBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1][0]).toBe(false)
  })
})
