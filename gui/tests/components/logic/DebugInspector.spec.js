import { describe, it, expect, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import DebugInspector from '@/components/logic/DebugInspector.vue'

describe('DebugInspector', () => {
  it('shows complete structured values and emits temporary override changes', async () => {
    const wrapper = mount(DebugInspector, {
      props: {
        inputs: [{
          id: 'payload',
          label: 'Payload',
          incoming: { nested: ['full value'] },
          effective: { nested: ['overridden value'] },
          overridden: true,
          capturedOverridden: true,
          locallyOverridden: false,
          overrideText: '',
        }],
        outputs: { result: { ok: true, text: 'x'.repeat(1000) } },
        metadata: { timestamp: '2026-07-21T12:00:00Z', duration_ms: 3.5, used_overrides: true },
      },
    })

    expect(wrapper.text()).toContain('full value')
    expect(wrapper.text()).toContain('overridden value')
    expect(wrapper.text()).toContain('Effektiver Wert')
    expect(wrapper.text()).toContain('x'.repeat(1000))
    expect(wrapper.classes()).toContain('border-amber-400')
    await wrapper.find('textarea').setValue('{"test":true}')
    expect(wrapper.emitted('set-override')[0]).toEqual(['payload', '{"test":true}'])
  })

  it('falls back to placeholders for incomplete execution metadata', async () => {
    const wrapper = mount(DebugInspector, {
      props: { outputs: { result: 1 }, metadata: { timestamp: '', used_overrides: false } },
    })

    expect(wrapper.text()).toContain('Ausgeführt: —')
    expect(wrapper.text()).toContain('Dauer: — ms')
    expect(wrapper.text()).not.toContain('Diese Ausführung verwendete temporäre Eingangsüberschreibungen.')
  })

  it('confirms individual and complete payload copies', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const wrapper = mount(DebugInspector, {
      props: { outputs: { result: 42 } },
    })

    await wrapper.find('button[title="Kopieren"]').trigger('click')
    expect(wrapper.text()).toContain('Kopiert!')
    await wrapper.findAll('button').find(button => button.text() === 'Nutzdaten kopieren').trigger('click')
    expect(wrapper.text()).toContain('Kopiert!')
    expect(writeText).toHaveBeenCalledTimes(2)
  })

  it('falls back to a hidden textarea when the Clipboard API is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    const execCommand = vi.fn().mockReturnValue(true)
    Object.defineProperty(document, 'execCommand', { value: execCommand, configurable: true })
    const wrapper = mount(DebugInspector, {
      props: { outputs: { result: 42 } },
    })

    await wrapper.find('button[title="Kopieren"]').trigger('click')
    await flushPromises()

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(wrapper.text()).toContain('Kopiert!')
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('falls back when the Clipboard API rejects the write', async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException('Not allowed', 'NotAllowedError'))
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const execCommand = vi.fn().mockReturnValue(true)
    Object.defineProperty(document, 'execCommand', { value: execCommand, configurable: true })
    const wrapper = mount(DebugInspector, {
      props: { outputs: { result: 42 } },
    })

    await wrapper.find('button[title="Kopieren"]').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledOnce()
    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(wrapper.text()).toContain('Kopiert!')
  })

  it('emits override clearing actions', async () => {
    const wrapper = mount(DebugInspector, {
      props: {
        inputs: [{ id: 'value', label: 'Value', incoming: null, overridden: true, locallyOverridden: true, overrideText: '42' }],
        hasOverrides: true,
      },
    })

    await wrapper.findAll('button').find(button => button.text() === 'Alle Überschreibungen löschen').trigger('click')
    await wrapper.findAll('button').find(button => button.text() === 'Löschen').trigger('click')

    expect(wrapper.emitted('clear-all')).toHaveLength(1)
    expect(wrapper.emitted('clear-override')[0]).toEqual(['value'])
  })
})
