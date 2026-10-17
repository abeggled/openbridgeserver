import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi:       { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:   { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
    authApi:     { login: vi.fn(), me: vi.fn() },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

async function mountPanel(data = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'n1', type: 'string_replace', data },
      nodeTypes: [{ type: 'string_replace', label: 'string_replace', description: '' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

function lastRules(wrapper) {
  return JSON.parse(wrapper.emitted('update').at(-1)[0].rules)
}

const RULES = JSON.stringify([
  { search: 'a', replace: '1', mode: 'plain', case_sensitive: true, replace_all: true },
  { search: 'b', replace: '2', mode: 'regex', case_sensitive: false, replace_all: false },
  { search: 'c', replace: '3', mode: 'plain', case_sensitive: true, replace_all: true },
])

describe('NodeConfigPanel string_replace', () => {
  it('renders one empty plain rule when nothing is stored', async () => {
    const w = await mountPanel({})
    await flushPromises()

    expect(w.find('[data-testid="replace-rule-0"]').exists()).toBe(true)
    expect(w.find('[data-testid="replace-rule-1"]').exists()).toBe(false)
    expect(w.find('[data-testid="replace-rule-search-0"]').element.value).toBe('')
    expect(w.find('[data-testid="replace-rule-mode-0"]').element.value).toBe('plain')
    expect(w.find('[data-testid="replace-rule-case-0"]').element.checked).toBe(true)
    expect(w.find('[data-testid="replace-rule-all-0"]').element.checked).toBe(true)
    w.unmount()
  })

  it('falls back to the default rule when the stored JSON is unparsable', async () => {
    const w = await mountPanel({ rules: '{' })
    await flushPromises()

    expect(w.find('[data-testid="replace-rule-0"]').exists()).toBe(true)
    expect(w.find('[data-testid="replace-rule-1"]').exists()).toBe(false)
    w.unmount()
  })

  it('renders rules stored as an array without rewriting them', async () => {
    const w = await mountPanel({ rules: JSON.parse(RULES) })
    await flushPromises()

    expect(w.find('[data-testid="replace-rule-2"]').exists()).toBe(true)
    expect(w.find('[data-testid="replace-rule-search-1"]').element.value).toBe('b')
    expect(w.find('[data-testid="replace-rule-mode-1"]').element.value).toBe('regex')
    expect(w.find('[data-testid="replace-rule-case-1"]').element.checked).toBe(false)
    expect(w.find('[data-testid="replace-rule-all-1"]').element.checked).toBe(false)
    expect(w.emitted('update')).toBeUndefined()
    w.unmount()
  })

  it('renders a rule without search and replace keys as empty fields', async () => {
    const w = await mountPanel({ rules: [{ mode: 'plain' }] })
    await flushPromises()

    expect(w.find('[data-testid="replace-rule-search-0"]').element.value).toBe('')
    expect(w.find('[data-testid="replace-rule-replacement-0"]').element.value).toBe('')
    w.unmount()
  })

  it.each([
    ['missing key', {}, true],
    ['real boolean true', { case_sensitive: true, replace_all: true }, true],
    ['real boolean false', { case_sensitive: false, replace_all: false }, false],
    ['null', { case_sensitive: null, replace_all: null }, false],
    ['zero', { case_sensitive: 0, replace_all: 0 }, false],
    ['the string "false"', { case_sensitive: 'false', replace_all: 'false' }, false],
    ['the string "Off"', { case_sensitive: ' Off ', replace_all: ' Off ' }, false],
    ['the string "true"', { case_sensitive: 'true', replace_all: 'true' }, true],
  ])('shows the flags the executor will actually use — %s', async (_name, flags, expected) => {
    const w = await mountPanel({ rules: [{ search: 'a', replace: 'b', mode: 'plain', ...flags }] })
    await flushPromises()

    expect(w.find('[data-testid="replace-rule-case-0"]').element.checked).toBe(expected)
    expect(w.find('[data-testid="replace-rule-all-0"]').element.checked).toBe(expected)
    w.unmount()
  })

  it.each([
    ['no mode key', {}, 'plain'],
    ['plain', { mode: 'plain' }, 'plain'],
    ['an unknown mode', { mode: 'fuzzy' }, 'plain'],
    ['regex', { mode: 'regex' }, 'regex'],
    ['regex in a different case with padding', { mode: ' REGEX ' }, 'regex'],
  ])('shows the mode the executor will actually use — %s', async (_name, rule, expected) => {
    const w = await mountPanel({ rules: [{ search: 'a', replace: 'b', ...rule }] })
    await flushPromises()

    expect(w.find('[data-testid="replace-rule-mode-0"]').element.value).toBe(expected)
    expect(w.find('[data-testid="replace-rule-search-0"]').attributes('placeholder'))
      .toBe(expected === 'regex' ? 'z.B. (\\d+)' : 'z.B. alt')
    w.unmount()
  })

  it('explains RegEx group references on a RegEx rule only', async () => {
    const w = await mountPanel({
      rules: [{ search: 'a', mode: 'plain' }, { search: '(a)', mode: 'regex' }],
    })
    await flushPromises()

    const hints = w.findAll('.form-group p').map(p => p.text()).filter(text => text.includes('\\1'))
    expect(hints).toHaveLength(1)
    expect(hints[0]).toContain('\\g<name>')
    w.unmount()
  })

  it('appends an empty plain rule', async () => {
    const w = await mountPanel({ rules: RULES })
    await flushPromises()

    await w.find('[data-testid="replace-rule-add"]').trigger('click')

    expect(lastRules(w)).toHaveLength(4)
    expect(lastRules(w)[3]).toEqual({ search: '', replace: '', mode: 'plain', case_sensitive: true, replace_all: true })
    w.unmount()
  })

  it('moves a rule up and down and keeps the ends clamped', async () => {
    const w = await mountPanel({ rules: RULES })
    await flushPromises()

    await w.find('[data-testid="replace-rule-down-0"]').trigger('click')
    expect(lastRules(w).map(r => r.search)).toEqual(['b', 'a', 'c'])

    await w.find('[data-testid="replace-rule-up-2"]').trigger('click')
    expect(lastRules(w).map(r => r.search)).toEqual(['b', 'c', 'a'])

    expect(w.find('[data-testid="replace-rule-up-0"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-testid="replace-rule-down-2"]').attributes('disabled')).toBeDefined()
    w.unmount()
  })

  // The remaining guards are defensive: the UI disables the buttons at the ends
  // and never addresses a row that is not rendered, so they are only reachable
  // by calling the handler directly.
  it('ignores a move beyond the list bounds', async () => {
    const w = await mountPanel({ rules: RULES })
    await flushPromises()

    w.vm.moveReplaceRule(0, -1)
    w.vm.moveReplaceRule(2, 1)

    expect(w.emitted('update')).toBeUndefined()
    w.unmount()
  })

  it('removes a rule but never the last one', async () => {
    const w = await mountPanel({ rules: RULES })
    await flushPromises()

    await w.find('[data-testid="replace-rule-remove-1"]').trigger('click')
    expect(lastRules(w).map(r => r.search)).toEqual(['a', 'c'])

    await w.find('[data-testid="replace-rule-remove-1"]').trigger('click')
    expect(lastRules(w).map(r => r.search)).toEqual(['a'])

    const remove = w.find('[data-testid="replace-rule-remove-0"]')
    expect(remove.attributes('disabled')).toBeDefined()
    await remove.trigger('click')
    expect(lastRules(w)).toHaveLength(1)
    w.unmount()
  })

  it('ignores a remove for an index that no longer exists', async () => {
    const w = await mountPanel({ rules: RULES })
    await flushPromises()

    w.vm.removeReplaceRule(9)

    expect(w.emitted('update')).toBeUndefined()
    w.unmount()
  })

  it('edits search text, replacement, mode and both switches of one rule', async () => {
    const w = await mountPanel({ rules: RULES })
    await flushPromises()

    const search = w.find('[data-testid="replace-rule-search-0"]')
    search.element.value = 'x'
    await search.trigger('input')
    expect(lastRules(w)[0].search).toBe('x')

    const replacement = w.find('[data-testid="replace-rule-replacement-0"]')
    replacement.element.value = 'y'
    await replacement.trigger('input')
    expect(lastRules(w)[0].replace).toBe('y')

    const mode = w.find('[data-testid="replace-rule-mode-0"]')
    mode.element.value = 'regex'
    await mode.trigger('change')
    expect(lastRules(w)[0].mode).toBe('regex')

    await w.find('[data-testid="replace-rule-case-0"]').setValue(false)
    expect(lastRules(w)[0].case_sensitive).toBe(false)

    await w.find('[data-testid="replace-rule-all-0"]').setValue(false)
    expect(lastRules(w)[0].replace_all).toBe(false)
    w.unmount()
  })

  it('ignores an edit for an index that no longer exists', async () => {
    const w = await mountPanel({ rules: RULES })
    await flushPromises()

    w.vm.updateReplaceRule(9, 'search', 'x')

    expect(w.emitted('update')).toBeUndefined()
    w.unmount()
  })

  it('switches the search field between plain and RegEx labelling', async () => {
    const w = await mountPanel({ rules: RULES })
    await flushPromises()

    expect(w.find('[data-testid="replace-rule-search-0"]').attributes('placeholder')).toBe('z.B. alt')
    expect(w.find('[data-testid="replace-rule-search-1"]').attributes('placeholder')).toBe('z.B. (\\d+)')
    w.unmount()
  })
})
