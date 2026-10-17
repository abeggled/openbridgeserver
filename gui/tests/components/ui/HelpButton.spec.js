import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import HelpButton from '@/components/ui/HelpButton.vue'
import { useHelpStore } from '@/stores/help'

vi.mock('@/api/client', () => ({
  helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
}))

function mountButton(props = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  return mount(HelpButton, { props: { helpId: 'datapoints-overview', ...props }, global: { plugins: [pinia] } })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('HelpButton', () => {
  it('renders a button with an accessible label', () => {
    const w = mountButton()
    const button = w.find('button')
    expect(button.exists()).toBe(true)
    expect(button.attributes('aria-label')).toBeTruthy()
  })

  it('opens the help drawer for its help_id on click', async () => {
    const w = mountButton({ helpId: 'datapoints-overview' })
    const store = useHelpStore()

    await w.find('button').trigger('click')

    expect(store.isOpen).toBe(true)
    expect(store.currentHelpId).toBe('datapoints-overview')
  })

  it('opens with the help_id from its own props, not a stale one', async () => {
    const w = mountButton({ helpId: 'logic-nodes' })
    const store = useHelpStore()

    await w.find('button').trigger('click')

    expect(store.currentHelpId).toBe('logic-nodes')
  })

  it('stays clickable when nested inside a pointer-events-none ancestor (issue feedback: several Settings tabs wrap their whole body in pointer-events-none in demo mode, and the help button often sits inside that wrapper)', () => {
    const w = mountButton()
    // pointer-events-auto on the button's own element lets it opt back in
    // even though a demo-mode ancestor sets pointer-events-none — real
    // browser cascade behaviour, not something happy-dom's layout engine
    // resolves, so this asserts the class is present rather than a computed
    // style or a simulated style through an actual pointer-events-none parent.
    expect(w.find('button').classes()).toContain('pointer-events-auto')
  })

  it('uses the default btn-icon size when compact is not set', () => {
    const w = mountButton()
    expect(w.find('button').classes()).toContain('btn-icon')
    expect(w.find('svg').classes()).toContain('w-4')
  })

  it('shrinks to a smaller footprint when compact is set (issue feedback: the default size nearly doubled row height in the Logic Module block palette)', () => {
    const w = mountButton({ compact: true })
    expect(w.find('button').classes()).not.toContain('btn-icon')
    expect(w.find('button').classes()).toContain('p-0.5')
    expect(w.find('svg').classes()).toContain('w-3')
  })
})
