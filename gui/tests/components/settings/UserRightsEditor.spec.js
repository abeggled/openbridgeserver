import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

let authzApi
let hierarchyApi

const ModalStub = {
  props: ['modelValue', 'title'],
  emits: ['update:modelValue'],
  template: '<div v-if="modelValue" data-testid="modal-stub"><slot /></div>',
}

const TREE_NODES = [
  {
    id: 'building',
    name: 'Gebäude',
    children: [
      { id: 'ground-floor', name: 'EG', children: [{ id: 'kitchen', name: 'Küche', children: [] }] },
      { id: 'upper-floor', name: 'OG', children: [] },
    ],
  },
]

function grant(nodeType, nodeId, role, effect = 'allow') {
  return { node_type: nodeType, node_id: nodeId, role, effect }
}

beforeEach(() => {
  vi.resetModules()
  authzApi = {
    getUserGrants: vi.fn().mockResolvedValue({
      data: {
        principal: { principal_type: 'user', principal_id: 'alice' },
        grants: [
          grant('hierarchy', 'kitchen', 'resident'),
          grant('datapoint', 'dp-denied', 'operator', 'deny'),
          grant('datapoint', 'dp-direct', 'guest'),
        ],
      },
    }),
    preview: vi.fn().mockResolvedValue({
      data: {
        results: ['read', 'write', 'activate', 'generate'].map((action) => ({
          action,
          node_type: 'hierarchy',
          node_id: 'kitchen',
          allowed: action !== 'generate',
          reason_text: action === 'generate' ? 'Denied by role.' : 'Allowed by role.',
        })),
      },
    }),
    updateUserGrants: vi.fn().mockResolvedValue({
      data: { principal: { principal_type: 'user', principal_id: 'alice' }, grants: [] },
    }),
  }
  hierarchyApi = {
    listTrees: vi.fn().mockResolvedValue({ data: [{ id: 'tree-1', name: 'Haus' }] }),
    getTreeNodes: vi.fn().mockResolvedValue({ data: TREE_NODES }),
  }
  vi.doMock('@/api/client', () => ({ authzApi, hierarchyApi }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountEditor() {
  const { default: UserRightsEditor } = await import('@/components/settings/UserRightsEditor.vue')
  const wrapper = mount(UserRightsEditor, {
    props: { modelValue: true, username: 'alice' },
    global: {
      stubs: {
        Modal: ModalStub,
        Spinner: { template: '<span data-testid="spinner" />' },
      },
    },
  })
  await flushPromises()
  return wrapper
}

async function next(wrapper) {
  await wrapper.get('[data-testid="rights-next"]').trigger('click')
  await flushPromises()
}

describe('UserRightsEditor', () => {
  it('roundtrips editable grants, previews all actions and preserves advanced grants', async () => {
    const wrapper = await mountEditor()

    expect(authzApi.getUserGrants).toHaveBeenCalledWith('alice')
    expect(hierarchyApi.getTreeNodes).toHaveBeenCalledWith('tree-1')
    expect(wrapper.get('input[value="resident"]').element.checked).toBe(true)

    await next(wrapper)
    expect(wrapper.get('[data-testid="rights-node-kitchen"]').text()).toContain('Haus › Gebäude › EG › Küche')
    expect(wrapper.get('[data-testid="rights-node-kitchen"] input').element.checked).toBe(true)

    await next(wrapper)
    expect(authzApi.preview).toHaveBeenCalledWith({
      principal: { principal_type: 'user', principal_id: 'alice' },
      actions: ['read', 'write', 'activate', 'generate'],
      targets: [{ node_type: 'hierarchy', node_id: 'kitchen' }],
      draft_grants: [
        { principal_type: 'user', principal_id: 'alice', ...grant('datapoint', 'dp-denied', 'operator', 'deny') },
        { principal_type: 'user', principal_id: 'alice', ...grant('datapoint', 'dp-direct', 'guest') },
        { principal_type: 'user', principal_id: 'alice', ...grant('hierarchy', 'kitchen', 'resident') },
      ],
      include_persisted: false,
    })
    expect(wrapper.get('[data-testid="preview-kitchen-read"]').text()).toContain('Erlaubt')
    expect(wrapper.get('[data-testid="preview-kitchen-read"]').text()).toContain('Allowed by role.')
    expect(wrapper.get('[data-testid="preview-kitchen-generate"]').text()).toContain('Verboten')

    await next(wrapper)
    expect(wrapper.get('[data-testid="advanced-grants-preserved"]').text()).toContain('2')
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', [
      grant('datapoint', 'dp-denied', 'operator', 'deny'),
      grant('datapoint', 'dp-direct', 'guest'),
      grant('hierarchy', 'kitchen', 'resident'),
    ])
    expect(wrapper.emitted('saved')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
  })

  it('guards mixed existing roles until a new role is selected explicitly', async () => {
    authzApi.getUserGrants.mockResolvedValue({
      data: {
        principal: { principal_type: 'user', principal_id: 'alice' },
        grants: [
          grant('hierarchy', 'kitchen', 'guest'),
          grant('hierarchy', 'upper-floor', 'operator'),
        ],
      },
    })
    const wrapper = await mountEditor()

    expect(wrapper.get('[data-testid="mixed-role-warning"]').text()).toContain('unterschiedliche Rollen')
    expect(wrapper.get('[data-testid="rights-next"]').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('input[type="radio"]').every((input) => !input.element.checked)).toBe(true)

    await wrapper.get('input[value="owner"]').setValue(true)
    expect(wrapper.get('[data-testid="rights-next"]').attributes('disabled')).toBeUndefined()
    await next(wrapper)
    await next(wrapper)
    await next(wrapper)
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    const savedGrants = authzApi.updateUserGrants.mock.calls[0][1]
    expect(savedGrants).toEqual([
      grant('hierarchy', 'kitchen', 'owner'),
      grant('hierarchy', 'upper-floor', 'owner'),
    ])
  })

  it('keeps an orphaned saved hierarchy assignment visible and selected', async () => {
    authzApi.getUserGrants.mockResolvedValue({
      data: {
        principal: { principal_type: 'user', principal_id: 'alice' },
        grants: [grant('hierarchy', 'missing-room', 'guest')],
      },
    })
    const wrapper = await mountEditor()
    await next(wrapper)

    const orphan = wrapper.get('[data-testid="rights-node-missing-room"]')
    expect(orphan.text()).toContain('nicht mehr')
    expect(orphan.get('input').element.checked).toBe(true)
    expect(wrapper.get('[data-testid="orphaned-scope-block"]').text()).toContain('Speichern gesperrt')
    expect(wrapper.get('[data-testid="rights-next"]').attributes('disabled')).toBeDefined()
  })

  it('shows hierarchy deny assignments and prevents a colliding allow selection', async () => {
    authzApi.getUserGrants.mockResolvedValue({
      data: {
        principal: { principal_type: 'user', principal_id: 'alice' },
        grants: [grant('hierarchy', 'kitchen', 'guest', 'deny')],
      },
    })
    const wrapper = await mountEditor()
    await wrapper.get('input[value="guest"]').setValue(true)
    await next(wrapper)

    const deniedScope = wrapper.get('[data-testid="rights-node-kitchen"]')
    expect(deniedScope.text()).toContain('Verbots-Zuweisung')
    expect(deniedScope.get('input').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="rights-next"]').attributes('disabled')).toBeDefined()
  })

  it('blocks a last-writer-wins overwrite when grants changed after opening', async () => {
    const initial = {
      principal: { principal_type: 'user', principal_id: 'alice' },
      grants: [grant('hierarchy', 'kitchen', 'resident')],
    }
    authzApi.getUserGrants
      .mockResolvedValueOnce({ data: initial })
      .mockResolvedValueOnce({ data: { ...initial, grants: [...initial.grants, grant('datapoint', 'new-dp', 'guest')] } })
    const wrapper = await mountEditor()
    await next(wrapper)
    await next(wrapper)
    await next(wrapper)
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="rights-save-error"]').text()).toContain('nach dem Öffnen')
    expect(authzApi.updateUserGrants).not.toHaveBeenCalled()
  })

  it('shows API errors without advancing or dropping the current draft', async () => {
    authzApi.preview.mockRejectedValue({ response: { data: { detail: 'Preview rejected' } } })
    const wrapper = await mountEditor()
    await next(wrapper)
    await next(wrapper)

    expect(wrapper.get('[data-testid="rights-preview-error"]').text()).toContain('Preview rejected')
    expect(wrapper.get('[data-testid="rights-scope-step"]').exists()).toBe(true)

    authzApi.preview.mockResolvedValue({ data: { results: [{ action: 'read', node_id: 'kitchen', allowed: true, reason_text: 'Allowed' }] } })
    await next(wrapper)
    await next(wrapper)
    authzApi.updateUserGrants.mockRejectedValue({ response: { data: { detail: 'Save rejected' } } })
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="rights-save-error"]').text()).toContain('Save rejected')
  })
})
