import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi:      { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:  { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountCommentPanel(data = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: {
        id: 'c1',
        type: 'comment',
        data: { text: '', width: 220, height: 140, ...data },
      },
      nodeTypes: [{ type: 'comment', label: 'Comment', description: 'A note.', config_schema: {} }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

describe('NodeConfigPanel comment', () => {
  it('renders a textarea bound to data.text', async () => {
    const wrapper = await mountCommentPanel({ text: 'Existing note' })
    await flushPromises()
    const textarea = wrapper.find('[data-testid="comment-text"]')
    expect(textarea.exists()).toBe(true)
    expect(textarea.element.value).toBe('Existing note')
    wrapper.unmount()
  })

  it('emits only the edited text, not the (possibly stale) width/height', async () => {
    // Regression test: width/height can be updated directly on the canvas
    // (CommentNode's resize handler) while the panel is open, bypassing
    // localData entirely. Emitting the whole localData object here would
    // clobber a fresh resize with the panel's stale snapshot.
    const wrapper = await mountCommentPanel({ text: '', width: 300, height: 180 })
    await flushPromises()
    const textarea = wrapper.find('[data-testid="comment-text"]')
    await textarea.setValue('New note text')
    await textarea.trigger('change')
    expect(wrapper.emitted('update')[0][0]).toEqual({ text: 'New note text' })
    wrapper.unmount()
  })

  it('does not render the generic config_schema field loop for comment nodes', async () => {
    const wrapper = await mountCommentPanel()
    await flushPromises()
    // width/height are in config_schema but must not appear as generic form fields —
    // the dedicated comment block only renders the text textarea.
    expect(wrapper.findAll('input[type="number"]').length).toBe(0)
    wrapper.unmount()
  })
})
