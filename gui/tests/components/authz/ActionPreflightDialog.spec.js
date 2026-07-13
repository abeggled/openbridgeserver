import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ActionPreflightDialog from '@/components/authz/ActionPreflightDialog.vue'

const ModalStub = {
  props: ['modelValue', 'title'],
  emits: ['update:modelValue'],
  template: '<div v-if="modelValue"><h2>{{ title }}</h2><slot /><slot name="footer" /></div>',
}

function mountDialog(props = {}) {
  return mount(ActionPreflightDialog, {
    props: {
      modelValue: true,
      title: 'Check action',
      items: [],
      ...props,
    },
    global: {
      stubs: { Modal: ModalStub, Spinner: { template: '<span data-testid="spinner" />' } },
    },
  })
}

describe('ActionPreflightDialog', () => {
  it('allows confirmation only when every backend requirement is allowed', async () => {
    const wrapper = mountDialog({
      items: [
        { id: 'graph', label: 'Graph ACTIVATE', allowed: true },
        { id: 'notify', label: 'Notification capability', allowed: true },
      ],
    })

    expect(wrapper.get('[data-testid="preflight-outcome"]').text()).toContain('Berechtigungen')
    expect(wrapper.get('[data-testid="preflight-confirm"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-testid="preflight-confirm"]').trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
  })

  it('shows denial reasons and blocks confirmation', () => {
    const wrapper = mountDialog({
      items: [{ id: 'sms', label: 'SMS capability', allowed: false, reason: 'Missing capability' }],
    })

    expect(wrapper.text()).toContain('Missing capability')
    expect(wrapper.get('[data-testid="preflight-outcome"]').text()).toContain('gesperrt')
    expect(wrapper.get('[data-testid="preflight-confirm"]').attributes('disabled')).toBeDefined()
  })

  it('keeps confirmation disabled while loading or after an error', async () => {
    const loading = mountDialog({ loading: true })
    expect(loading.get('[data-testid="spinner"]').exists()).toBe(true)
    expect(loading.get('[data-testid="preflight-confirm"]').attributes('disabled')).toBeDefined()

    await loading.setProps({ loading: false, error: 'Preflight failed' })
    expect(loading.get('[role="alert"]').text()).toBe('Preflight failed')
    expect(loading.get('[data-testid="preflight-confirm"]').attributes('disabled')).toBeDefined()
  })
})
