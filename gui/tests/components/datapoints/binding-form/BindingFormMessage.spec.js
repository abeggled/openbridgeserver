import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormMessage from '@/components/datapoints/binding-form/BindingFormMessage.vue'

function mk({ cfg = {}, selectedInstance = null } = {}) {
  return mount(BindingFormMessage, {
    props: {
      cfg: {
        operator: '==',
        compare_value: 'true',
        title: '',
        cooldown_seconds: 0,
        message: '###DPN###: ###DP###',
        providers: [],
        send_on_change: true,
        ...cfg,
      },
      selectedInstance: selectedInstance ?? {
        config: {
          providers: {
            pushover: { enabled: true, targets: { phone: {} } },
            telegram: { enabled: true, targets: { family: {} } },
            disabled: { enabled: false, targets: { hidden: {} } },
          },
        },
      },
    },
  })
}

describe('BindingFormMessage', () => {
  it('hides compare value when operator is any', async () => {
    const wrapper = mk()
    expect(wrapper.findAll('input').some(input => input.element.value === 'true')).toBe(true)

    await wrapper.find('select').setValue('any')

    expect(wrapper.findAll('input').some(input => input.element.value === 'true')).toBe(false)
  })

  it('adds and removes provider targets from enabled providers', async () => {
    const cfg = { providers: [] }
    const wrapper = mk({ cfg })

    await wrapper.findAll('button').find(button => button.text() === 'Ziel hinzufügen').trigger('click')

    expect(cfg.providers).toEqual([{ provider: 'pushover', target: 'phone' }])
    expect(wrapper.text()).not.toContain('hidden')

    await wrapper.findAll('button').find(button => button.text() === 'Löschen').trigger('click')
    expect(cfg.providers).toEqual([])
  })

  it('does not expose targets from string-disabled providers', async () => {
    const cfg = { providers: [] }
    const wrapper = mk({
      cfg,
      selectedInstance: {
        config: {
          providers: {
            pushover: { enabled: 'false', targets: { hidden: {} } },
            telegram: { enabled: 'true', targets: { family: {} } },
          },
        },
      },
    })

    await wrapper.findAll('button').find(button => button.text() === 'Ziel hinzufügen').trigger('click')

    expect(cfg.providers).toEqual([{ provider: 'telegram', target: 'family' }])
    expect(wrapper.text()).not.toContain('hidden')
  })

  it('does not add duplicate provider target pairs', async () => {
    const cfg = { providers: [] }
    const wrapper = mk({
      cfg,
      selectedInstance: {
        config: {
          providers: {
            pushover: { enabled: true, targets: { phone: {} } },
          },
        },
      },
    })
    const addButton = () => wrapper.findAll('button').find(button => button.text() === 'Ziel hinzufügen')

    await addButton().trigger('click')

    expect(cfg.providers).toEqual([{ provider: 'pushover', target: 'phone' }])
    expect(addButton().attributes('disabled')).toBeDefined()
  })

  it('changes target when provider selection changes', async () => {
    const cfg = { providers: [{ provider: 'pushover', target: 'phone' }] }
    const wrapper = mk({ cfg })

    await wrapper.findAll('select')[1].setValue('telegram')

    expect(cfg.providers[0]).toEqual({ provider: 'telegram', target: 'family' })
  })

  it('updates editable text and boolean fields', async () => {
    const cfg = { title: '', cooldown_seconds: 0, message: 'old', send_on_change: true, providers: [] }
    const wrapper = mk({ cfg })
    const formCfg = wrapper.props('cfg')

    const inputs = wrapper.findAll('input')
    await inputs.find(input => input.element.value === '').setValue('Alarm')
    await wrapper.find('input[type="number"]').setValue(42)
    await wrapper.find('textarea').setValue('new template')
    await wrapper.find('input[type="checkbox"]').setChecked(false)

    expect(formCfg.title).toBe('Alarm')
    expect(formCfg.cooldown_seconds).toBe(42)
    expect(formCfg.message).toBe('new template')
    expect(formCfg.send_on_change).toBe(false)
  })

  it('disables add target when no configured provider targets exist', () => {
    const wrapper = mk({ selectedInstance: { config: { providers: {} } } })

    expect(wrapper.findAll('button').find(button => button.text() === 'Ziel hinzufügen').attributes('disabled')).toBeDefined()
  })
})
