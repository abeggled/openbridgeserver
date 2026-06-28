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
    await wrapper.findAll('input[type="password"]')[1].setValue('user-key')

    expect(getModel().providers.pushover.targets.home.user_key).toBe('user-key')

    await wrapper.findAll('button').find(button => button.text() === 'Löschen').trigger('click')
    await flushPromises()
    expect(getModel().providers.pushover.targets.home).toBeUndefined()
  })

  it('adds a seven.io target with SMS default and allows changing to voice', async () => {
    const { wrapper, getModel } = mountForm()
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    const addButtons = wrapper.findAll('button').filter(button => button.text() === 'Ziel hinzufügen')

    await checkboxes[2].setChecked(true)
    await flushPromises()
    await addButtons[2].trigger('click')
    await flushPromises()
    expect(getModel().providers['seven.io'].targets.default.channel).toBe('sms')

    const channelSelect = wrapper.findAll('select').find(select => select.find('option[value="voice"]').exists())
    await channelSelect.setValue('voice')
    expect(getModel().providers['seven.io'].targets.default.channel).toBe('voice')
  })

  it('updates a Telegram chat target', async () => {
    const { wrapper, getModel } = mountForm()
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    const addButtons = wrapper.findAll('button').filter(button => button.text() === 'Ziel hinzufügen')

    await checkboxes[1].setChecked(true)
    await flushPromises()
    await wrapper.find('input[type="password"]').setValue('bot-token')
    await addButtons[1].trigger('click')
    await flushPromises()

    const textInputs = wrapper.findAll('input').filter(input => input.attributes('type') === 'text')
    await textInputs[textInputs.length - 1].setValue('123456')

    expect(getModel().providers.telegram.bot_token).toBe('bot-token')
    expect(getModel().providers.telegram.targets.default.chat_id).toBe('123456')
  })
})
