import { describe, it, expect } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import MessageConfigForm from '@/components/adapters/MessageConfigForm.vue'

function mountForm(initial = {}) {
  let model = {
    providers: {},
    ...initial,
  }
  let wrapper
  wrapper = mount(MessageConfigForm, {
    props: {
      modelValue: model,
      'onUpdate:modelValue': async (next) => {
        model = next
        await wrapper.setProps({ modelValue: model })
      },
    },
  })
  return { wrapper, getModel: () => model }
}

describe('MessageConfigForm', () => {
  it('hides target buttons for disabled providers', () => {
    const { wrapper } = mountForm()

    expect(wrapper.findAll('button').filter(button => button.text() === 'Ziel hinzufügen')).toHaveLength(0)
  })

  it('enables a provider and updates provider fields', async () => {
    const { wrapper, getModel } = mountForm()

    await wrapper.find('input[type="checkbox"]').setChecked(true)
    await flushPromises()
    await wrapper.find('input[type="password"]').setValue('pushover-token')

    expect(getModel().providers.pushover.enabled).toBe(true)
    expect(getModel().providers.pushover.api_token).toBe('pushover-token')
  })

  it('adds, renames, updates, and removes a Pushover target', async () => {
    const { wrapper, getModel } = mountForm()

    await wrapper.find('input[type="checkbox"]').setChecked(true)
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === 'Ziel hinzufügen').trigger('click')
    await flushPromises()

    const targetInputs = wrapper.findAll('input').filter(input => input.attributes('type') !== 'checkbox')
    await targetInputs[1].setValue('home')
    await targetInputs[1].trigger('change')
    await flushPromises()
    const passwordInputs = wrapper.findAll('input[type="password"]')
    await passwordInputs[passwordInputs.length - 1].setValue('user-key')

    expect(getModel().providers.pushover.targets.home.user_key).toBe('user-key')
    expect(getModel().providers.pushover.targets.default).toBeUndefined()

    const deleteButtons = wrapper.findAll('button').filter(button => button.text() === 'Löschen')
    await deleteButtons[deleteButtons.length - 1].trigger('click')
    await flushPromises()
    expect(getModel().providers.pushover.targets.home).toBeUndefined()
  })

  it('adds a seven.io target with SMS default and allows changing to voice', async () => {
    const { wrapper, getModel } = mountForm()
    const checkboxes = wrapper.findAll('input[type="checkbox"]')

    await checkboxes[2].setChecked(true)
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === 'Ziel hinzufügen').trigger('click')
    await flushPromises()
    expect(getModel().providers['seven.io'].targets.default.channel).toBe('sms')

    const channelSelect = wrapper.findAll('select').find(select => select.find('option[value="voice"]').exists())
    await channelSelect.setValue('voice')
    expect(getModel().providers['seven.io'].targets.default.channel).toBe('voice')
  })

  it('creates unique seven.io target names and updates recipient', async () => {
    const { wrapper, getModel } = mountForm({
      providers: {
        'seven.io': { enabled: true, targets: { default: { channel: 'sms' } } },
      },
    })

    await wrapper.findAll('button').find(button => button.text() === 'Ziel hinzufügen').trigger('click')
    await flushPromises()
    const textInputs = wrapper.findAll('input').filter(input => input.attributes('type') === 'text')
    await textInputs[textInputs.length - 1].setValue('+4100000000')

    expect(getModel().providers['seven.io'].targets.target_2.channel).toBe('sms')
    expect(getModel().providers['seven.io'].targets.target_2.to).toBe('+4100000000')
  })

  it('updates a Telegram chat target', async () => {
    const { wrapper, getModel } = mountForm()
    const checkboxes = wrapper.findAll('input[type="checkbox"]')

    await checkboxes[1].setChecked(true)
    await flushPromises()
    await wrapper.find('input[type="password"]').setValue('bot-token')
    await wrapper.findAll('button').find(button => button.text() === 'Ziel hinzufügen').trigger('click')
    await flushPromises()

    const textInputs = wrapper.findAll('input').filter(input => input.attributes('type') === 'text')
    await textInputs[textInputs.length - 1].setValue('123456')

    expect(getModel().providers.telegram.bot_token).toBe('bot-token')
    expect(getModel().providers.telegram.targets.default.chat_id).toBe('123456')
  })

  it('ignores empty and duplicate target renames', async () => {
    const { wrapper, getModel } = mountForm({
      providers: {
        pushover: { enabled: true, targets: { default: {}, other: {} } },
      },
    })
    const targetNameInputs = wrapper.findAll('input').filter(input => input.attributes('type') !== 'checkbox' && input.element.value !== undefined)

    await targetNameInputs[0].setValue('')
    await targetNameInputs[0].trigger('change')
    await targetNameInputs[0].setValue('other')
    await targetNameInputs[0].trigger('change')

    expect(Object.keys(getModel().providers.pushover.targets).sort()).toEqual(['default', 'other'])
  })
})
