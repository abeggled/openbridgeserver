import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import DebugInspector from '@/components/logic/DebugInspector.vue'

describe('DebugInspector', () => {
  it('shows complete structured values and emits temporary override changes', async () => {
    const wrapper = mount(DebugInspector, {
      props: {
        node: { id: 'n1', data: { label: 'Parser' } },
        inputs: [{ id: 'payload', label: 'Payload', incoming: { nested: ['full value'] }, overridden: false, overrideText: '' }],
        outputs: { result: { ok: true, text: 'x'.repeat(1000) } },
        metadata: { timestamp: '2026-07-21T12:00:00Z', duration_ms: 3.5, used_overrides: true },
      },
    })

    expect(wrapper.text()).toContain('full value')
    expect(wrapper.text()).toContain('x'.repeat(1000))
    expect(wrapper.classes()).toContain('border-amber-400')
    await wrapper.find('textarea').setValue('{"test":true}')
    expect(wrapper.emitted('set-override')[0]).toEqual(['payload', '{"test":true}'])
  })

  it('confirms individual and complete payload copies', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const wrapper = mount(DebugInspector, {
      props: { node: { id: 'n1', data: {} }, outputs: { result: 42 } },
    })

    await wrapper.find('button[title="Kopieren"]').trigger('click')
    expect(wrapper.text()).toContain('Kopiert!')
    await wrapper.findAll('button').find(button => button.text() === 'Nutzdaten kopieren').trigger('click')
    expect(wrapper.text()).toContain('Kopiert!')
    expect(writeText).toHaveBeenCalledTimes(2)
  })
})
