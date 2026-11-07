import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PushoverOptionsFields from '@/components/notifications/PushoverOptionsFields.vue'

function mk(options = {}, priority = null, hint = '') {
  return mount(PushoverOptionsFields, {
    props: { options, priority, hint, 'onUpdate:priority': () => {} },
  })
}

describe('PushoverOptionsFields', () => {
  it('defaults the format to plain text and shows the hint', () => {
    const options = {}
    const wrapper = mk(options, null, 'Platzhalter-Hinweis')

    expect(options.format).toBeUndefined()
    expect(wrapper.find('[data-testid="pushover-format"]').element.value).toBe('text')
    expect(wrapper.text()).toContain('Platzhalter-Hinweis')
    expect(wrapper.find('[data-testid="pushover-priority"]').element.value).toBe('')
  })

  it('keeps a valid format and hides the empty hint', () => {
    const options = { format: 'monospace' }
    const wrapper = mk(options)

    expect(wrapper.find('[data-testid="pushover-format"]').element.value).toBe('monospace')
    expect(wrapper.findAll('p.hint')).toHaveLength(2)
  })

  it('edits text and number options in place and emits change', async () => {
    const options = {}
    const wrapper = mk(options)

    await wrapper.find('[data-testid="pushover-image-url"]').setValue('http://192.168.1.50/snapshot.jpg')
    await wrapper.find('[data-testid="pushover-url"]').setValue('https://obs.example')
    await wrapper.find('[data-testid="pushover-url-title"]').setValue('Open')
    await wrapper.find('[data-testid="pushover-sound"]').setValue('siren')
    await wrapper.find('[data-testid="pushover-ttl"]').setValue('600')
    await wrapper.find('[data-testid="pushover-timestamp"]').setValue('1700000000')
    await wrapper.find('[data-testid="pushover-format"]').setValue('html')

    expect(options).toMatchObject({
      image_url: 'http://192.168.1.50/snapshot.jpg',
      url: 'https://obs.example',
      url_title: 'Open',
      sound: 'siren',
      ttl: 600,
      timestamp: 1700000000,
      format: 'html',
    })
    expect(wrapper.emitted('change').length).toBeGreaterThanOrEqual(7)
  })

  it('shows retry and expire only for emergency priority and validates them', async () => {
    const options = {}
    const wrapper = mk(options, 1)
    expect(wrapper.find('[data-testid="pushover-retry"]').exists()).toBe(false)

    await wrapper.find('[data-testid="pushover-priority"]').setValue('2')
    expect(wrapper.emitted('update:priority').at(-1)).toEqual([2])
    await wrapper.setProps({ priority: 2 })

    expect(wrapper.find('[data-testid="pushover-error-retry"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="pushover-error-expire"]').exists()).toBe(true)

    await wrapper.find('[data-testid="pushover-retry"]').setValue('10')
    expect(wrapper.find('[data-testid="pushover-error-retry"]').text()).toContain('30')
    await wrapper.find('[data-testid="pushover-retry"]').setValue('60')
    await wrapper.find('[data-testid="pushover-expire"]').setValue('600')
    expect(wrapper.find('[data-testid="pushover-error-retry"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="pushover-error-expire"]').exists()).toBe(false)

    await wrapper.find('[data-testid="pushover-priority"]').setValue('')
    expect(wrapper.emitted('update:priority').at(-1)).toEqual([null])
  })

  it('shows inline errors for invalid combinations', () => {
    const wrapper = mk({ url_title: 'Open', sound: 'bad sound', ttl: 0, timestamp: 0 })

    expect(wrapper.find('[data-testid="pushover-error-url_title"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="pushover-error-sound"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="pushover-error-ttl"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="pushover-error-timestamp"]').exists()).toBe(true)
  })

  it('accepts a URL title alone when the URL may come from an input', () => {
    const wrapper = mount(PushoverOptionsFields, { props: { options: { url_title: 'Open' }, urlFromInput: true } })
    expect(wrapper.find('[data-testid="pushover-error-url_title"]').exists()).toBe(false)
  })

  it('gives each instance its own sound list', () => {
    const first = mk().find('datalist').attributes('id')
    const second = mk().find('datalist').attributes('id')
    expect(first).not.toBe(second)
  })

  it('shows length errors with the limit', () => {
    const wrapper = mk({ image_url: 'x'.repeat(2049), url: 'x'.repeat(513) })

    expect(wrapper.find('[data-testid="pushover-error-image_url"]').text()).toContain('2048')
    expect(wrapper.find('[data-testid="pushover-error-url"]').text()).toContain('512')
  })
})
