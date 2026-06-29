import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import NodePalette from '@/components/logic/NodePalette.vue'

const NODE_TYPES = [
  { type: 'and',          label: 'AND',     category: 'logic', color: '#4ade80' },
  { type: 'or',           label: 'OR',      category: 'logic', color: '#4ade80' },
  { type: 'math_formula', label: 'Formula', category: 'math',  color: '#60a5fa' },
]

function mockStorage(initial = {}) {
  const store = { ...initial }
  const storage = {
    getItem:    vi.fn(k => store[k] ?? null),
    setItem:    vi.fn((k, v) => { store[k] = v }),
    removeItem: vi.fn(k => { delete store[k] }),
    clear:      vi.fn(),
  }
  Object.defineProperty(window,      'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis,  'localStorage', { value: storage, configurable: true })
  return storage
}

function mountPalette(props = {}) {
  return mount(NodePalette, { props: { nodeTypes: NODE_TYPES, ...props } })
}

// ── Expanded state ─────────────────────────────────────────────────────────

describe('NodePalette — expanded', () => {
  beforeEach(() => mockStorage())

  it('renders all block items', () => {
    const wrapper = mountPalette()
    expect(wrapper.findAll('.cursor-grab')).toHaveLength(3)
    expect(wrapper.text()).toContain('AND')
    expect(wrapper.text()).toContain('OR')
  })

  it('emits toggle when the column header is clicked', async () => {
    const wrapper = mountPalette()
    await wrapper.findAll('button')[0].trigger('click')
    expect(wrapper.emitted('toggle')).toHaveLength(1)
  })

  it('adds display:none to the block list when a section header is clicked', async () => {
    const wrapper = mountPalette()
    expect(wrapper.html()).not.toContain('display: none')

    await wrapper.findAll('button')[1].trigger('click')

    expect(wrapper.html()).toContain('display: none')
  })

  it('removes display:none when the same section header is clicked again', async () => {
    const wrapper = mountPalette()
    const sectionBtn = wrapper.findAll('button')[1]
    await sectionBtn.trigger('click')
    await sectionBtn.trigger('click')
    expect(wrapper.html()).not.toContain('display: none')
  })

  it('persists collapsed category ids to localStorage', async () => {
    const storage = mockStorage()
    const wrapper = mountPalette()
    await wrapper.findAll('button')[1].trigger('click')
    expect(storage.setItem).toHaveBeenCalledWith(
      'logic_palette_collapsed_cats',
      expect.stringContaining('logic')
    )
  })

  it('restores previously collapsed categories from localStorage', () => {
    mockStorage({ logic_palette_collapsed_cats: '["logic"]' })
    const wrapper = mountPalette()
    expect(wrapper.html()).toContain('display: none')
  })

  it('falls back to no collapsed categories when localStorage value is malformed JSON', () => {
    mockStorage({ logic_palette_collapsed_cats: 'not-json{{{' })
    const wrapper = mountPalette()
    expect(wrapper.findAll('.cursor-grab')).toHaveLength(3)
    expect(wrapper.html()).not.toContain('display: none')
  })

  it('falls back to no collapsed categories when localStorage value is non-array JSON', () => {
    mockStorage({ logic_palette_collapsed_cats: '"logic"' })
    const wrapper = mountPalette()
    expect(wrapper.findAll('.cursor-grab')).toHaveLength(3)
    expect(wrapper.html()).not.toContain('display: none')
  })

  it('emits drag-start and sets dataTransfer on dragstart', () => {
    const wrapper = mountPalette()
    const block = wrapper.findAll('.cursor-grab')[0]
    const dt = { setData: vi.fn(), effectAllowed: null }
    block.trigger('dragstart', { dataTransfer: dt })
    expect(dt.setData).toHaveBeenCalledWith('application/vueflow-node-type', 'and')
    expect(wrapper.emitted('drag-start')).toBeTruthy()
  })
})

// ── Collapsed state ────────────────────────────────────────────────────────

describe('NodePalette — collapsed column', () => {
  beforeEach(() => mockStorage())

  it('does not render block items when collapsed', () => {
    const wrapper = mountPalette({ collapsed: true })
    expect(wrapper.findAll('.cursor-grab')).toHaveLength(0)
  })

  it('emits toggle when the expand button is clicked', async () => {
    const wrapper = mountPalette({ collapsed: true })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('toggle')).toHaveLength(1)
  })
})
