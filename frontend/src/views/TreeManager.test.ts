// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TreeManager from './TreeManager.vue'

const mocks = vi.hoisted(() => {
  const node = {
    id: 'node-1',
    parent_id: null,
    name: 'Home',
    type: 'PAGE',
    order: 0,
    icon: null,
    access: 'public',
    page_config: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
  return {
    node,
    updateNode: vi.fn(),
    setNodeUsers: vi.fn(),
    getNodeUsers: vi.fn().mockResolvedValue([]),
    listUsers: vi.fn().mockResolvedValue([
      { id: 'user-1', username: 'alice', is_admin: false },
    ]),
  }
})

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      params ? `${key}:${JSON.stringify(params)}` : key,
  }),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({
    nodes: [mocks.node],
    rootNodes: [mocks.node],
    loadTree: vi.fn().mockResolvedValue(undefined),
    getNode: (id: string) => id === mocks.node.id ? mocks.node : undefined,
    updateNode: mocks.updateNode,
    createNode: vi.fn(),
    deleteNode: vi.fn(),
    copyNode: vi.fn(),
    moveNode: vi.fn(),
  }),
}))

vi.mock('@/stores/theme', () => ({
  useThemeStore: () => ({ isDark: false, toggle: vi.fn() }),
}))

vi.mock('@/api/client', () => ({
  visu: {
    getNodeUsers: mocks.getNodeUsers,
    setNodeUsers: mocks.setNodeUsers,
    exportNode: vi.fn(),
    importNodes: vi.fn(),
  },
  users: { list: mocks.listUsers },
}))

function mountManager() {
  return mount(TreeManager, {
    global: {
      mocks: { $t: (key: string) => key },
      stubs: {
        AuthButton: true,
        IconPicker: true,
        VisuIcon: true,
      },
    },
  })
}

async function selectUserAccess(wrapper: ReturnType<typeof mountManager>) {
  await wrapper.get('[data-testid="tree-node-node-1"]').trigger('click')
  const userRadio = wrapper.findAll('input[type="radio"]').find(
    input => (input.element as HTMLInputElement).value === 'user',
  )
  expect(userRadio).toBeDefined()
  await userRadio!.setValue(true)
  await flushPromises()
}

describe('TreeManager access target updates', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.node.access = 'public'
    mocks.getNodeUsers.mockResolvedValue([])
    mocks.listUsers.mockResolvedValue([
      { id: 'user-1', username: 'alice', is_admin: false },
    ])
    mocks.updateNode.mockResolvedValue(mocks.node)
  })

  it('saves access and assigned users in one atomic node update', async () => {
    const wrapper = mountManager()
    await flushPromises()
    await selectUserAccess(wrapper)

    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('[data-testid="save-node"]').trigger('click')
    await flushPromises()

    expect(mocks.updateNode).toHaveBeenCalledTimes(1)
    expect(mocks.updateNode).toHaveBeenCalledWith('node-1', expect.objectContaining({
      access: 'user',
      usernames: ['alice'],
    }))
    expect(mocks.setNodeUsers).not.toHaveBeenCalled()
  })

  it('preserves assignments when an existing user audience cannot be loaded', async () => {
    mocks.node.access = 'user'
    mocks.getNodeUsers.mockRejectedValue(new Error('Forbidden'))
    const wrapper = mountManager()
    await flushPromises()

    await wrapper.get('[data-testid="tree-node-node-1"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="save-node"]').trigger('click')
    await flushPromises()

    expect(mocks.updateNode).toHaveBeenCalledTimes(1)
    const patch = mocks.updateNode.mock.calls[0][1]
    expect(patch).toEqual(expect.objectContaining({ access: 'user' }))
    expect(patch).not.toHaveProperty('usernames')
  })

  it('renders the stable datapoint policy error with actionable details', async () => {
    mocks.updateNode.mockRejectedValue(Object.assign(new Error(), {
      code: 'visu_target_audience_datapoints_denied',
      details: {
        username: 'alice',
        datapoint_ids: ['blocked.dp'],
      },
    }))
    const wrapper = mountManager()
    await flushPromises()
    await selectUserAccess(wrapper)

    await wrapper.get('[data-testid="save-node"]').trigger('click')
    await flushPromises()

    const error = wrapper.get('[data-testid="save-error"]').text()
    expect(error).toContain('tree.targetDatapointsDenied')
    expect(error).toContain('alice')
    expect(error).toContain('blocked.dp')
  })
})
