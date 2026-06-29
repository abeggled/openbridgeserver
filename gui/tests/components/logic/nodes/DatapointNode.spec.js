import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const { HANDLE_STUB, removeNodesMock } = vi.hoisted(() => ({
  HANDLE_STUB:     { template: '<div class="handle" :data-type="type" :data-id="id" />', props: ['type', 'id', 'position', 'style'] },
  removeNodesMock: vi.fn(),
}))

vi.mock('@vue-flow/core', () => ({
  Handle:    HANDLE_STUB,
  Position:  { Left: 'left', Right: 'right' },
  useVueFlow: () => ({ removeNodes: removeNodesMock }),
}))

async function mountDp(overrides = {}) {
  const { default: DatapointNode } = await import('@/components/logic/nodes/DatapointNode.vue')
  return mount(DatapointNode, {
    props: {
      id:   'dp-1',
      type: 'datapoint_read',
      data: {},
      ...overrides,
    },
    global: { stubs: { Handle: HANDLE_STUB } },
  })
}

describe('DatapointNode — datapoint_read', () => {
  beforeEach(() => removeNodesMock.mockClear())

  it('shows placeholder when no datapoint_name', async () => {
    const w = await mountDp()
    await flushPromises()
    expect(w.text()).toContain('nicht gewählt')
  })

  it('shows datapoint_name when provided', async () => {
    const w = await mountDp({ data: { datapoint_name: 'Temperatur' } })
    await flushPromises()
    expect(w.text()).toContain('Temperatur')
  })

  it('renders source handles for read type', async () => {
    const w = await mountDp({ type: 'datapoint_read' })
    await flushPromises()
    const sourceHandles = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(sourceHandles.length).toBe(2)
  })

  it('renders target handles for write type', async () => {
    const w = await mountDp({ type: 'datapoint_write', data: {} })
    await flushPromises()
    const targetHandles = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(targetHandles.length).toBe(2)
  })

  it('shows filter badge when value_formula is set', async () => {
    const w = await mountDp({ data: { value_formula: 'x * 2' } })
    await flushPromises()
    expect(w.text()).toContain('⊘')
  })

  it('does not show filter badge when no filter fields set', async () => {
    const w = await mountDp({ data: {} })
    await flushPromises()
    expect(w.text()).not.toContain('⊘')
  })

  it('shows debug band when data._dbg is set', async () => {
    const w = await mountDp({ data: { _dbg: '42.5' } })
    await flushPromises()
    expect(w.find('[data-testid="debug-band"]').exists()).toBe(true)
    expect(w.find('[data-testid="debug-band"]').text()).toBe('42.5')
  })

  it('calls removeNodes on delete button click', async () => {
    const w = await mountDp()
    await flushPromises()
    await w.trigger('mouseenter')
    const deleteBtn = w.find('.gn-delete')
    await deleteBtn.trigger('click')
    expect(removeNodesMock).toHaveBeenCalledWith(['dp-1'])
  })
})
