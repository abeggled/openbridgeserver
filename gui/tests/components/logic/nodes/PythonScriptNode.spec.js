import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const { HANDLE_STUB, removeNodesMock } = vi.hoisted(() => ({
  HANDLE_STUB:     { template: '<div class="handle" :data-type="type" :data-id="id" />', props: ['type', 'id', 'position', 'style'] },
  removeNodesMock: vi.fn(),
}))

vi.mock('@vue-flow/core', () => ({
  Handle:     HANDLE_STUB,
  Position:   { Left: 'left', Right: 'right' },
  useVueFlow: () => ({ removeNodes: removeNodesMock }),
}))

async function mountPy(overrides = {}) {
  const { default: PythonScriptNode } = await import('@/components/logic/nodes/PythonScriptNode.vue')
  return mount(PythonScriptNode, {
    props: {
      id:   'py-1',
      type: 'python_script',
      data: {},
      ...overrides,
    },
    global: { stubs: { Handle: HANDLE_STUB } },
  })
}

describe('PythonScriptNode', () => {
  beforeEach(() => removeNodesMock.mockClear())

  it('shows default script comment when no script provided', async () => {
    const w = await mountPy()
    await flushPromises()
    expect(w.find('pre').text()).toContain('# script')
  })

  it('shows the script when provided', async () => {
    const w = await mountPy({ data: { script: 'return a + b' } })
    await flushPromises()
    expect(w.find('pre').text()).toContain('return a + b')
  })

  it('truncates long scripts to 80 chars with ellipsis', async () => {
    const longScript = 'x = ' + 'a'.repeat(100)
    const w = await mountPy({ data: { script: longScript } })
    await flushPromises()
    const text = w.find('pre').text()
    expect(text.length).toBeLessThanOrEqual(80)
    expect(text).toContain('…')
  })

  it('renders 3 target handles (a, b, c)', async () => {
    const w = await mountPy()
    await flushPromises()
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(targets.length).toBe(3)
  })

  it('renders 1 source handle (result)', async () => {
    const w = await mountPy()
    await flushPromises()
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(sources.length).toBe(1)
  })

  it('shows "Python Script" header', async () => {
    const w = await mountPy()
    await flushPromises()
    expect(w.find('.gn-label').text()).toBe('Python Script')
  })

  it('shows debug value when data._dbg is set', async () => {
    const w = await mountPy({ data: { _dbg: 'result=99' } })
    await flushPromises()
    expect(w.text()).toContain('result=99')
  })

  it('calls removeNodes on delete button click', async () => {
    const w = await mountPy()
    await flushPromises()
    await w.trigger('mouseenter')
    await w.find('.gn-delete').trigger('click')
    expect(removeNodesMock).toHaveBeenCalledWith(['py-1'])
  })
})
