// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActiveVisuAlarm } from '@/api/client'
import { useDeviceStore } from '@/stores/device'

const alarms = ref<ActiveVisuAlarm[]>([])
const acknowledge = vi.fn()
const tone = vi.fn()

vi.mock('@/composables/useVisuAlarms', () => ({ useVisuAlarms: () => ({ alarms }) }))
vi.mock('@/api/client', () => ({ visuAlarms: { acknowledge: (id: string) => acknowledge(id) }, visuDevices: { list: vi.fn() } }))
vi.mock('@/utils/alarmTone', () => ({ playAlarmTone: () => tone() }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string, p?: Record<string, unknown>) => (p ? `${key}:${JSON.stringify(p)}` : key) }) }))
vi.mock('@/components/VisuIcon.vue', () => ({ default: { props: ['icon'], template: '<span data-testid="visu-icon">{{ icon }}</span>' } }))

const { default: AlarmOverlay } = await import('./AlarmOverlay.vue')

function make(overrides: Partial<ActiveVisuAlarm> = {}): ActiveVisuAlarm {
  return {
    id: 'post', name: 'Post', display: 'message', title: 'title-post', text: 'text-post', icon: '📬',
    severity: 'info', page_id: null, ack_button: true, sound: false, devices: [], since: '2026-10-19T10:00:00Z', ...overrides,
  }
}

let wrapper: VueWrapper | null = null
function mountOverlay() {
  wrapper = mount(AlarmOverlay, { attachTo: document.body })
  return wrapper
}
const q = (testid: string) => document.querySelector(`[data-testid="${testid}"]`)

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  alarms.value = []
  acknowledge.mockReset()
  tone.mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('AlarmOverlay', () => {
  it('shows nothing without alarms', () => {
    mountOverlay()
    expect(q('alarm-overlay')).toBeNull()
  })

  it('shows a message alarm with icon, title, text and acknowledge button', async () => {
    alarms.value = [make({ severity: 'critical' })]
    mountOverlay()
    await flushPromises()

    expect(q('alarm-icon')?.textContent).toBe('📬')
    expect(q('alarm-title')?.textContent).toBe('title-post')
    expect(q('alarm-text')?.textContent).toBe('text-post')
    expect(q('alarm-message')?.classList.contains('border-red-600')).toBe(true)
    expect(q('alarm-overlay')?.getAttribute('aria-label')).toBe('title-post')
    expect(q('alarm-waiting')).toBeNull()
  })

  it('leaves out empty parts and the button for alarms that end on their own', async () => {
    alarms.value = [make({ icon: '', title: '', text: 'text-only', ack_button: false, severity: 'warning' })]
    mountOverlay()
    await flushPromises()

    expect(q('alarm-icon')).toBeNull()
    expect(q('alarm-title')).toBeNull()
    expect(q('alarm-text')?.textContent).toBe('text-only')
    expect(q('alarm-acknowledge')).toBeNull()
    expect(q('alarm-message')?.classList.contains('border-orange-500')).toBe(true)
    expect(q('alarm-overlay')?.getAttribute('aria-label')).toBe('Post')
  })

  it('acknowledges the shown alarm and reports a failed attempt', async () => {
    alarms.value = [make()]
    mountOverlay()
    await flushPromises()

    acknowledge.mockResolvedValueOnce(undefined)
    ;(q('alarm-acknowledge') as HTMLButtonElement).click()
    await flushPromises()
    expect(acknowledge).toHaveBeenCalledWith('post')
    expect(q('alarm-failed')).toBeNull()

    acknowledge.mockRejectedValueOnce(new Error('404'))
    ;(q('alarm-acknowledge') as HTMLButtonElement).click()
    await flushPromises()
    expect(q('alarm-failed')?.textContent).toBe('alarm.acknowledgeFailed')
  })

  it('shows a page alarm in an embedded frame', async () => {
    alarms.value = [make({ display: 'page', page_id: 'page 1', title: '' }), make({ id: 'phone' })]
    mountOverlay()
    await flushPromises()

    expect(q('alarm-message')).toBeNull()
    expect(q('alarm-frame')?.getAttribute('src')).toBe('/visu/page%201?embed=1')
    expect(q('alarm-page')?.textContent).toContain('Post')
    expect(q('alarm-waiting')?.textContent).toBe('alarm.waiting:{"n":1}')

    acknowledge.mockRejectedValueOnce(new Error('404'))
    ;(q('alarm-acknowledge') as HTMLButtonElement).click()
    await flushPromises()
    expect(q('alarm-failed')).not.toBeNull()

    // The note belongs to the alarm whose acknowledge failed, not to the next one shown.
    alarms.value = [make({ id: 'phone' })]
    await flushPromises()
    expect(q('alarm-title')?.textContent).toBe('title-post')
    expect(q('alarm-failed')).toBeNull()
  })

  it('page alarms without acknowledge button and without page id', async () => {
    alarms.value = [make({ display: 'page', page_id: null, ack_button: false })]
    mountOverlay()
    await flushPromises()

    expect(q('alarm-frame')?.getAttribute('src')).toBe('')
    expect(q('alarm-acknowledge')).toBeNull()
  })

  it('shows only alarms meant for this device', async () => {
    alarms.value = [make({ id: 'panel-only', devices: ['panel'] }), make({ id: 'all', title: 'title-all' })]
    mountOverlay()
    await flushPromises()
    expect(q('alarm-title')?.textContent).toBe('title-all') // no profile: only alarms for every Visu

    useDeviceStore().select('panel')
    await flushPromises()
    expect(q('alarm-title')?.textContent).toBe('title-post')
    expect(q('alarm-waiting')?.textContent).toBe('alarm.waiting:{"n":1}')

    useDeviceStore().select('handy')
    alarms.value = [make({ devices: ['panel'] })]
    await flushPromises()
    expect(q('alarm-overlay')).toBeNull()
  })

  it('sounds once per newly arrived alarm with sound', async () => {
    alarms.value = [make({ sound: true }), make({ id: 'quiet' })]
    mountOverlay()
    await flushPromises()
    expect(tone).toHaveBeenCalledTimes(1)

    alarms.value = [...alarms.value] // refreshed list, same alarms
    await flushPromises()
    expect(tone).toHaveBeenCalledTimes(1)

    alarms.value = [make({ id: 'quiet' })] // post ended
    await flushPromises()
    alarms.value = [make({ sound: true }), make({ id: 'quiet' })] // and came back
    await flushPromises()
    expect(tone).toHaveBeenCalledTimes(2)
  })
})
