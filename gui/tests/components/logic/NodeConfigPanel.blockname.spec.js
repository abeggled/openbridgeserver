/**
 * Issue #1157 — the block config panel (which carries the debug values tab
 * since #1128) is the second place a block can be renamed. Its header shows
 * the user-defined name as the heading and demotes the block type and the
 * generated node id to a secondary line.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi:       { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:   { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountPanel({ node, nodeTypes } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  const w = mount(mod.default, {
    props: {
      node: node ?? { id: 'clamp-1787342537851', type: 'clamp', data: { min: 0, max: 100 } },
      nodeTypes: nodeTypes ?? [{
        type: 'clamp',
        label: 'Limiter',
        description: '',
        config_schema: { min: { type: 'number', default: 0 }, max: { type: 'number', default: 100 } },
      }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
  await flushPromises()
  return w
}

describe('NodeConfigPanel — block name (#1157)', () => {
  it('shows the block type default title as the placeholder while unnamed', async () => {
    const w = await mountPanel()
    const input = w.get('[data-testid="node-label-input"]')
    expect(input.element.value).toBe('')
    expect(input.attributes('placeholder')).toBe('Begrenzer')
  })

  it('shows the custom name in the rename field', async () => {
    const w = await mountPanel({
      node: { id: 'clamp-1', type: 'clamp', data: { label: 'Sollwert Begrenzung' } },
    })
    expect(w.get('[data-testid="node-label-input"]').element.value).toBe('Sollwert Begrenzung')
  })

  it('keeps the block type and the generated id as secondary information', async () => {
    const w = await mountPanel()
    const identity = w.get('[data-testid="node-identity"]')
    expect(identity.text()).toContain('Begrenzer')
    expect(identity.text()).toContain('clamp-1787342537851')
  })

  it('falls back to the catalogue label when the type has no translation', async () => {
    const w = await mountPanel({
      node: { id: 'x-1', type: 'not_translated', data: {} },
      nodeTypes: [{ type: 'not_translated', label: 'Catalogue Label', config_schema: {} }],
    })
    expect(w.get('[data-testid="node-label-input"]').attributes('placeholder')).toBe('Catalogue Label')
    expect(w.get('[data-testid="node-identity"]').text()).toContain('Catalogue Label')
  })

  it('falls back to the raw type when the block is unknown entirely', async () => {
    const w = await mountPanel({
      node: { id: 'x-1', type: 'not_translated', data: {} },
      nodeTypes: [],
    })
    expect(w.get('[data-testid="node-label-input"]').attributes('placeholder')).toBe('not_translated')
  })

  it('does not save anything when a never-named block is committed untouched', async () => {
    const w = await mountPanel()
    await w.get('[data-testid="node-label-input"]').trigger('change')
    expect(w.emitted('update')).toBeUndefined()
  })

  it('does not save anything when the name is re-committed unchanged', async () => {
    const w = await mountPanel({
      node: { id: 'clamp-1', type: 'clamp', data: { label: 'Sollwert' } },
    })
    const input = w.get('[data-testid="node-label-input"]')
    await input.setValue('  Sollwert  ')
    await input.trigger('change')
    expect(w.emitted('update')).toBeUndefined()
    expect(input.element.value).toBe('Sollwert')
  })

  it('emits the new name on change', async () => {
    const w = await mountPanel()
    const input = w.get('[data-testid="node-label-input"]')
    await input.setValue('Sollwert Begrenzung')
    await input.trigger('change')
    const updates = w.emitted('update')
    expect(updates.at(-1)[0].label).toBe('Sollwert Begrenzung')
  })

  it('emits the new name on Enter', async () => {
    const w = await mountPanel()
    const input = w.get('[data-testid="node-label-input"]')
    await input.setValue('Sollwert Begrenzung')
    await input.trigger('keydown.enter')
    expect(w.emitted('update').at(-1)[0].label).toBe('Sollwert Begrenzung')
  })

  it('trims the name before emitting it', async () => {
    const w = await mountPanel()
    const input = w.get('[data-testid="node-label-input"]')
    await input.setValue('   Sollwert   ')
    await input.trigger('change')
    expect(w.emitted('update').at(-1)[0].label).toBe('Sollwert')
  })

  it('clears a whitespace-only name so the default title applies again', async () => {
    const w = await mountPanel({
      node: { id: 'clamp-1', type: 'clamp', data: { label: 'Alt' } },
    })
    const input = w.get('[data-testid="node-label-input"]')
    await input.setValue('   ')
    await input.trigger('change')
    expect(w.emitted('update').at(-1)[0].label).toBe('')
  })

  it('keeps the other block settings intact when only the name changes', async () => {
    const w = await mountPanel()
    const input = w.get('[data-testid="node-label-input"]')
    await input.setValue('Sollwert')
    await input.trigger('change')
    const payload = w.emitted('update').at(-1)[0]
    expect(payload.min).toBe(0)
    expect(payload.max).toBe(100)
  })

  it('reloads the field when another block is selected', async () => {
    const w = await mountPanel({
      node: { id: 'clamp-1', type: 'clamp', data: { label: 'Erster' } },
    })
    await w.setProps({ node: { id: 'clamp-2', type: 'clamp', data: { label: 'Zweiter' } } })
    await flushPromises()
    expect(w.get('[data-testid="node-label-input"]').element.value).toBe('Zweiter')
    expect(w.get('[data-testid="node-identity"]').text()).toContain('clamp-2')
  })

  it('still offers the close button next to the rename field', async () => {
    const w = await mountPanel()
    await w.get('button.btn-icon').trigger('click')
    expect(w.emitted('close')).toBeTruthy()
  })
})
