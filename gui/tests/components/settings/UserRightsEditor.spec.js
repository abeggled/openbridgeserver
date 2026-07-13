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

const REASONS_BY_ACTION = {
  read: 'allowed',
  write: 'direct_datapoint_grant',
  activate: 'admin',
  generate: 'explicit_deny',
}

function grant(nodeType, nodeId, role, effect = 'allow', centralControl = false) {
  return { node_type: nodeType, node_id: nodeId, role, effect, central_control: centralControl }
}

beforeEach(() => {
  vi.resetModules()
  authzApi = {
    getUserGrants: vi.fn().mockResolvedValue({
      headers: { etag: '"grants-v1"' },
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
          reason: REASONS_BY_ACTION[action],
          reason_text: 'Backend English reason.',
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
  vi.doMock('@/api/client', () => ({ hierarchyApi }))
  vi.doMock('@/api/authz', () => ({ authzApi }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/api/authz')
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
      targets: [
        { node_type: 'hierarchy', node_id: 'kitchen', control_class: 'central_plant' },
        { node_type: 'datapoint', node_id: 'dp-denied' },
        { node_type: 'datapoint', node_id: 'dp-direct' },
      ],
      draft_grants: [
        { principal_type: 'user', principal_id: 'alice', ...grant('datapoint', 'dp-denied', 'operator', 'deny') },
        { principal_type: 'user', principal_id: 'alice', ...grant('datapoint', 'dp-direct', 'guest') },
        { principal_type: 'user', principal_id: 'alice', ...grant('hierarchy', 'kitchen', 'resident') },
      ],
      include_persisted: false,
    })
    expect(wrapper.get('[data-testid="preview-kitchen-read"]').text()).toContain('Erlaubt')
    expect(wrapper.get('[data-testid="preview-kitchen-read"]').text()).toContain('passende Rollenzuweisung')
    expect(wrapper.get('[data-testid="preview-kitchen-write"]').text()).toContain('direkte Datenpunkt-Zuweisung')
    expect(wrapper.get('[data-testid="preview-kitchen-activate"]').text()).toContain('Administrator-Brücke')
    expect(wrapper.get('[data-testid="preview-kitchen-generate"]').text()).toContain('ausdrückliche Verbots-Zuweisung')
    expect(wrapper.text()).not.toContain('Backend English reason.')
    expect(wrapper.get('[data-testid="preview-kitchen-generate"]').text()).toContain('Verboten')

    await next(wrapper)
    expect(wrapper.get('[data-testid="rights-role-summary"]').text()).toContain('Bestehende Rollen bleiben erhalten')
    expect(wrapper.get('[data-testid="advanced-grants-preserved"]').text()).toContain('2')
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', [
      grant('datapoint', 'dp-denied', 'operator', 'deny'),
      grant('datapoint', 'dp-direct', 'guest'),
      grant('hierarchy', 'kitchen', 'resident'),
    ], '"grants-v1"')
    expect(wrapper.emitted('saved')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')).toContainEqual([false])
  })

  it('edits central plant control independently per scope and shows its preview and confirmation effect', async () => {
    authzApi.getUserGrants.mockResolvedValue({
      headers: { etag: '"central-v1"' },
      data: {
        principal: { principal_type: 'user', principal_id: 'alice' },
        grants: [
          grant('hierarchy', 'kitchen', 'resident', 'allow', true),
          { node_type: 'hierarchy', node_id: 'upper-floor', role: 'resident', effect: 'allow' },
        ],
      },
    })
    authzApi.preview.mockResolvedValue({
      data: {
        results: [
          {
            action: 'write',
            node_type: 'hierarchy',
            node_id: 'kitchen',
            allowed: false,
            reason: 'central_control_required',
            reason_text: 'Backend English reason.',
          },
          {
            action: 'write',
            node_type: 'hierarchy',
            node_id: 'upper-floor',
            allowed: true,
            reason: 'allowed',
            reason_text: 'Backend English reason.',
          },
        ],
      },
    })
    const wrapper = await mountEditor()
    await next(wrapper)

    const kitchenSwitch = wrapper.get('[data-testid="central-control-kitchen"]')
    const upperFloorSwitch = wrapper.get('[data-testid="central-control-upper-floor"]')
    expect(kitchenSwitch.element.checked).toBe(true)
    expect(upperFloorSwitch.element.checked).toBe(false)

    await kitchenSwitch.setValue(false)
    await upperFloorSwitch.setValue(true)
    expect(kitchenSwitch.element.checked).toBe(false)
    expect(upperFloorSwitch.element.checked).toBe(true)

    await next(wrapper)
    expect(authzApi.preview).toHaveBeenCalledWith({
      principal: { principal_type: 'user', principal_id: 'alice' },
      actions: ['read', 'write', 'activate', 'generate'],
      targets: [
        { node_type: 'hierarchy', node_id: 'kitchen', control_class: 'central_plant' },
        { node_type: 'hierarchy', node_id: 'upper-floor', control_class: 'central_plant' },
      ],
      draft_grants: [
        { principal_type: 'user', principal_id: 'alice', ...grant('hierarchy', 'kitchen', 'resident') },
        { principal_type: 'user', principal_id: 'alice', ...grant('hierarchy', 'upper-floor', 'resident', 'allow', true) },
      ],
      include_persisted: false,
    })
    expect(wrapper.get('[data-testid="preview-central-control-kitchen"]').text()).toContain('deaktiviert')
    expect(wrapper.get('[data-testid="preview-central-control-upper-floor"]').text()).toContain('aktiviert')
    expect(wrapper.get('[data-testid="preview-kitchen-write"]').text()).toContain('Zentralanlagensteuerung')
    expect(wrapper.text()).not.toContain('Backend English reason.')

    await next(wrapper)
    expect(wrapper.get('[data-testid="confirm-central-control-kitchen"]').text()).toContain('deaktiviert')
    expect(wrapper.get('[data-testid="confirm-central-control-upper-floor"]').text()).toContain('aktiviert')
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', [
      grant('hierarchy', 'kitchen', 'resident'),
      grant('hierarchy', 'upper-floor', 'resident', 'allow', true),
    ], '"central-v1"')
  })

  it('adds the closed create-graph capability and its central-plant authority', async () => {
    const wrapper = await mountEditor()
    await next(wrapper)

    expect(wrapper.get('[data-testid="logic-create-enabled"]').element.checked).toBe(false)
    await wrapper.get('[data-testid="logic-create-enabled"]').setValue(true)
    await wrapper.get('[data-testid="logic-create-central-control"]').setValue(true)
    await next(wrapper)

    expect(authzApi.preview).toHaveBeenCalledWith(expect.objectContaining({
      targets: expect.arrayContaining([
        { node_type: 'logic_capability', node_id: 'create_graph' },
      ]),
      draft_grants: expect.arrayContaining([
        {
          principal_type: 'user',
          principal_id: 'alice',
          ...grant('logic_capability', 'create_graph', 'operator', 'allow', true),
        },
      ]),
    }))

    await next(wrapper)
    expect(wrapper.get('[data-testid="logic-create-summary"]').text()).toContain('Aktiviert')
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', [
      grant('datapoint', 'dp-denied', 'operator', 'deny'),
      grant('datapoint', 'dp-direct', 'guest'),
      grant('hierarchy', 'kitchen', 'resident'),
      grant('logic_capability', 'create_graph', 'operator', 'allow', true),
    ], '"grants-v1"')
  })

  it('preserves per-node roles while adding and removing scopes in a mixed assignment', async () => {
    authzApi.getUserGrants.mockResolvedValue({
      headers: { etag: '"mixed-v1"' },
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
    expect(wrapper.get('[data-testid="rights-next"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.findAll('input[type="radio"]').every((input) => !input.element.checked)).toBe(true)
    expect(wrapper.get('[data-testid="bulk-role-reassign"]').attributes('disabled')).toBeDefined()

    await wrapper.get('input[value="resident"]').setValue(true)
    await next(wrapper)
    await wrapper.get('[data-testid="rights-node-upper-floor"] input').setValue(false)
    await wrapper.get('[data-testid="rights-node-building"] input').setValue(true)
    await next(wrapper)
    await next(wrapper)
    expect(wrapper.get('[data-testid="rights-role-summary"]').text()).toContain('neue Bereiche verwenden Bewohner')
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', [
      grant('hierarchy', 'building', 'resident'),
      grant('hierarchy', 'kitchen', 'guest'),
    ], '"mixed-v1"')
  })

  it('bulk-reassigns retained existing scopes only after explicit confirmation', async () => {
    authzApi.getUserGrants.mockResolvedValue({
      headers: { etag: '"mixed-v2"' },
      data: {
        principal: { principal_type: 'user', principal_id: 'alice' },
        grants: [
          grant('hierarchy', 'kitchen', 'guest'),
          grant('hierarchy', 'upper-floor', 'operator'),
        ],
      },
    })
    const wrapper = await mountEditor()
    await wrapper.get('input[value="owner"]').setValue(true)
    await wrapper.get('[data-testid="bulk-role-reassign"]').setValue(true)
    await next(wrapper)
    await next(wrapper)
    await next(wrapper)
    expect(wrapper.get('[data-testid="rights-role-summary"]').text()).toContain('Rolle Eigentümer neu zugewiesen')
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', [
      grant('hierarchy', 'kitchen', 'owner'),
      grant('hierarchy', 'upper-floor', 'owner'),
    ], '"mixed-v2"')
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
    expect(wrapper.get('[data-testid="rights-next"]').attributes('disabled')).toBeUndefined()
  })

  it('previews removed scopes as targets and preserves advanced grants when clearing editable scopes', async () => {
    authzApi.preview.mockResolvedValue({
      data: {
        results: ['read', 'write', 'activate', 'generate'].map((action) => ({
          action,
          node_type: 'hierarchy',
          node_id: 'kitchen',
          allowed: false,
          reason: 'missing_allow',
          reason_text: 'Backend English reason.',
        })),
      },
    })
    const wrapper = await mountEditor()
    await next(wrapper)
    await wrapper.get('[data-testid="rights-node-kitchen"] input').setValue(false)
    await next(wrapper)

    expect(wrapper.find('[data-testid="rights-preview-empty"]').exists()).toBe(false)
    expect(authzApi.preview).toHaveBeenCalledWith(expect.objectContaining({
      targets: [
        { node_type: 'hierarchy', node_id: 'kitchen', control_class: 'central_plant' },
        { node_type: 'datapoint', node_id: 'dp-denied' },
        { node_type: 'datapoint', node_id: 'dp-direct' },
      ],
      draft_grants: [
        { principal_type: 'user', principal_id: 'alice', ...grant('datapoint', 'dp-denied', 'operator', 'deny') },
        { principal_type: 'user', principal_id: 'alice', ...grant('datapoint', 'dp-direct', 'guest') },
      ],
    }))
    expect(wrapper.get('[data-testid="preview-kitchen-read"]').text()).toContain('Verboten')

    await next(wrapper)
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()
    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', [
      grant('datapoint', 'dp-denied', 'operator', 'deny'),
      grant('datapoint', 'dp-direct', 'guest'),
    ], '"grants-v1"')
  })

  it('can persist an explicitly empty grant set', async () => {
    authzApi.getUserGrants.mockResolvedValue({
      headers: { etag: '"clear-v1"' },
      data: {
        principal: { principal_type: 'user', principal_id: 'alice' },
        grants: [grant('hierarchy', 'kitchen', 'guest')],
      },
    })
    const wrapper = await mountEditor()
    await next(wrapper)
    await wrapper.get('[data-testid="rights-node-kitchen"] input').setValue(false)
    await next(wrapper)
    await next(wrapper)
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    expect(authzApi.preview).toHaveBeenCalledWith(expect.objectContaining({
      targets: [{ node_type: 'hierarchy', node_id: 'kitchen', control_class: 'central_plant' }],
      draft_grants: [],
    }))
    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', [], '"clear-v1"')
  })

  it('uses a local empty preview only when no editable or advanced targets exist', async () => {
    authzApi.getUserGrants.mockResolvedValue({
      headers: { etag: '"empty-v1"' },
      data: {
        principal: { principal_type: 'user', principal_id: 'alice' },
        grants: [],
      },
    })
    const wrapper = await mountEditor()
    await next(wrapper)
    await next(wrapper)

    expect(wrapper.get('[data-testid="rights-preview-empty"]').text()).toContain('keine bearbeitbaren')
    expect(authzApi.preview).not.toHaveBeenCalled()

    await next(wrapper)
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()
    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', [], '"empty-v1"')
  })

  it('invalidates the preview after explicit advanced removal and previews the removed target again', async () => {
    authzApi.getUserGrants.mockResolvedValue({
      headers: { etag: '"advanced-v1"' },
      data: {
        principal: { principal_type: 'user', principal_id: 'alice' },
        grants: [grant('datapoint', 'orphan-dp', 'guest', 'deny')],
      },
    })
    authzApi.preview.mockImplementation((body) => Promise.resolve({
      data: {
        results: body.targets.flatMap((target) => ['read', 'write', 'activate', 'generate'].map((action) => ({
          ...target,
          action,
          allowed: false,
          reason: 'missing_allow',
          reason_text: 'Backend English reason.',
        }))),
      },
    }))
    const wrapper = await mountEditor()
    await next(wrapper)
    await next(wrapper)
    await next(wrapper)
    expect(wrapper.get('[data-testid="advanced-grant-0"]').text()).toContain('orphan-dp')
    await wrapper.get('[data-testid="remove-advanced-grant-0"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="rights-preview-step"]').exists()).toBe(true)
    expect(authzApi.preview).toHaveBeenCalledTimes(2)
    expect(authzApi.preview.mock.calls[1][0]).toEqual(expect.objectContaining({
      targets: [{ node_type: 'datapoint', node_id: 'orphan-dp' }],
      draft_grants: [],
    }))

    await next(wrapper)
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', [], '"advanced-v1"')
  })

  it('keeps the draft open and shows the concurrency message on a 412 response', async () => {
    authzApi.updateUserGrants.mockRejectedValue({ response: { status: 412, data: { detail: 'stale' } } })
    const wrapper = await mountEditor()
    await next(wrapper)
    await next(wrapper)
    await next(wrapper)
    await wrapper.get('[data-testid="rights-save"]').trigger('click')
    await flushPromises()

    expect(authzApi.getUserGrants).toHaveBeenCalledTimes(1)
    expect(authzApi.updateUserGrants).toHaveBeenCalledWith('alice', expect.any(Array), '"grants-v1"')
    expect(wrapper.get('[data-testid="rights-save-error"]').text()).toContain('nach dem Öffnen')
    expect(wrapper.get('[data-testid="rights-confirm-step"]').exists()).toBe(true)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
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
