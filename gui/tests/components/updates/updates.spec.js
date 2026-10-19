import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { reactive } from 'vue'

const ROUTER_LINK_STUB = { template: '<a :href="to"><slot /></a>', props: ['to'] }
const MODAL_STUB = {
  name: 'Modal',
  props: ['modelValue', 'title'],
  emits: ['update:modelValue'],
  template: '<div v-if="modelValue" class="modal-stub" :data-title="title"><button class="modal-close" @click="$emit(\'update:modelValue\', false)" /><slot /></div>',
}

function baseStatus(overrides = {}) {
  return {
    enabled: true,
    channel: 'stable',
    channels: ['stable', 'pre-release', 'nightly'],
    installed_version: '2026.9.0',
    latest_version: '2026.10.0',
    latest_url: 'https://github.com/o/r/releases/tag/2026.10.0',
    update_available: true,
    last_check_at: '2026-10-17T10:00:00+00:00',
    last_success_at: '2026-10-17T10:00:00+00:00',
    next_check_at: '2026-10-17T22:00:00+00:00',
    last_error: null,
    last_error_detail: null,
    stale: false,
    checking: false,
    release_notes_count: 2,
    ...overrides,
  }
}

let store
let isAdmin
let isLoggedIn

function makeStore(status) {
  store = reactive({
    status,
    releaseNotes: [],
    notesLoading: false,
    notesError: false,
    checking: false,
    saving: false,
    statusError: false,
    get updateAvailable() { return this.status?.update_available === true },
    fetchStatus: vi.fn(async () => store.status),
    fetchReleaseNotes: vi.fn(async () => store.releaseNotes),
    checkNow: vi.fn(async () => store.status),
    setChannel: vi.fn(async (channel) => { store.status = { ...store.status, channel } }),
    requestUpdatesTab: vi.fn(),
  })
  return store
}

beforeEach(() => {
  vi.resetModules()
  isAdmin = true
  vi.doMock('@/stores/updates', () => ({ useUpdatesStore: () => store }))
  isLoggedIn = true
  vi.doMock('@/stores/auth', () => ({ useAuthStore: () => ({ isAdmin, isLoggedIn }) }))
  vi.doMock('@/components/ui/Modal.vue', () => ({ default: MODAL_STUB }))
  vi.doMock('@/components/ui/HelpButton.vue', () => ({ default: { name: 'HelpButton', props: ['helpId'], template: '<span class="help-stub" :data-help="helpId" />' } }))
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.doUnmock('@/stores/updates')
  vi.doUnmock('@/stores/auth')
  vi.doUnmock('@/components/ui/Modal.vue')
  vi.doUnmock('@/components/ui/HelpButton.vue')
  vi.useRealTimers()
})

async function mountComponent(path, props = {}) {
  const { default: Component } = await import(path)
  const wrapper = mount(Component, { props, global: { stubs: { RouterLink: ROUTER_LINK_STUB } } })
  await flushPromises()
  return wrapper
}

// ---------------------------------------------------------------------------
// UpdateNotice
// ---------------------------------------------------------------------------

describe('UpdateNotice', () => {
  it('shows channel, versions, check time and the release notes action', async () => {
    makeStore(baseStatus())
    const w = await mountComponent('@/components/updates/UpdateNotice.vue')
    expect(store.fetchStatus).toHaveBeenCalledTimes(1)
    expect(w.find('[data-testid="update-notice"]').exists()).toBe(true)
    expect(w.text()).toContain('Neue Version 2026.10.0 verfügbar')
    const details = w.find('[data-testid="update-notice-details"]').text()
    expect(details).toContain('Kanal: Stable')
    expect(details).toContain('installiert: 2026.9.0')
    expect(details).toContain('neueste: 2026.10.0')
    expect(details).not.toContain('—')
    expect(w.find('[data-testid="update-notice-stale"]').exists()).toBe(false)
    expect(w.find('.help-stub').attributes('data-help')).toBe('settings-updates')
    // Ctrl/Cmd/Shift/Alt-click opens a new tab or window: this one stays put.
    for (const modifier of ['ctrlKey', 'metaKey', 'shiftKey', 'altKey']) {
      await w.find('[data-testid="update-notice-settings"]').trigger('click', { [modifier]: true })
    }
    expect(store.requestUpdatesTab).not.toHaveBeenCalled()
    await w.find('[data-testid="update-notice-settings"]').trigger('click')
    expect(store.requestUpdatesTab).toHaveBeenCalledTimes(1)

    expect(w.find('.modal-stub').exists()).toBe(false)
    await w.find('[data-testid="update-notice-notes"]').trigger('click')
    await flushPromises()
    expect(w.find('.modal-stub').exists()).toBe(true)
    expect(store.fetchReleaseNotes).toHaveBeenCalled()
    await w.find('.modal-close').trigger('click')
    expect(w.find('.modal-stub').exists()).toBe(false)
  })

  it('keeps the open release notes when the update disappears', async () => {
    makeStore(baseStatus())
    const w = await mountComponent('@/components/updates/UpdateNotice.vue')
    await w.find('[data-testid="update-notice-notes"]').trigger('click')
    await flushPromises()
    // A poll, a manual check or a channel switch clears update_available.
    store.status = baseStatus({ update_available: false })
    await flushPromises()
    expect(w.find('[data-testid="update-notice"]').exists()).toBe(false)
    expect(w.find('.modal-stub').exists()).toBe(true)
  })

  it('marks a possibly outdated result', async () => {
    makeStore(baseStatus({ stale: true, last_error: 'network' }))
    const w = await mountComponent('@/components/updates/UpdateNotice.vue')
    expect(w.find('[data-testid="update-notice-stale"]').exists()).toBe(true)
  })

  it.each([
    ['no update', baseStatus({ update_available: false })],
    ['unknown', baseStatus({ update_available: null })],
    ['not loaded', null],
  ])('stays hidden when %s', async (_label, status) => {
    makeStore(status)
    const w = await mountComponent('@/components/updates/UpdateNotice.vue')
    expect(w.find('[data-testid="update-notice"]').exists()).toBe(false)
  })

  it('does not ask for the status without a login (no 401 redirect loop)', async () => {
    vi.useFakeTimers()
    isLoggedIn = false
    makeStore(null)
    const w = await mountComponent('@/components/updates/UpdateNotice.vue')
    vi.advanceTimersByTime(15 * 60 * 1000)
    expect(store.fetchStatus).not.toHaveBeenCalled()
    w.unmount()
  })

  it('polls the cached status and stops on unmount', async () => {
    vi.useFakeTimers()
    makeStore(baseStatus({ update_available: false }))
    const w = await mountComponent('@/components/updates/UpdateNotice.vue')
    vi.advanceTimersByTime(15 * 60 * 1000)
    expect(store.fetchStatus).toHaveBeenCalledTimes(2)
    w.unmount()
    vi.advanceTimersByTime(15 * 60 * 1000)
    expect(store.fetchStatus).toHaveBeenCalledTimes(2)
  })
})

// ---------------------------------------------------------------------------
// ReleaseNotesModal
// ---------------------------------------------------------------------------

describe('ReleaseNotesModal', () => {
  it('loads and renders the notes per version without executing HTML', async () => {
    makeStore(baseStatus())
    store.releaseNotes = [
      { version: '2026.9.1', name: 'Release 2026.9.1', published_at: '2026-09-08T20:22:13Z', url: 'https://github.com/o/r/releases/tag/2026.9.1', body: '* fix <img src=x onerror="alert(1)">' },
      { version: '2026.10.0', name: 'Release 2026.10.0', published_at: null, url: 'javascript:alert(1)', body: '### New\n<script>alert(1)</script>' },
    ]
    const w = await mountComponent('@/components/updates/ReleaseNotesModal.vue', { modelValue: true })
    expect(store.fetchReleaseNotes).toHaveBeenCalledTimes(1)
    expect(w.find('.modal-stub').attributes('data-title')).toBe('Release Notes')
    expect(w.text()).toContain('Änderungen nach 2026.9.0 bis einschließlich 2026.10.0')
    const sections = w.findAll('section')
    expect(sections.map((s) => s.attributes('data-testid'))).toEqual(['release-note-2026.9.1', 'release-note-2026.10.0'])
    expect(sections[0].find('a').attributes('href')).toBe('https://github.com/o/r/releases/tag/2026.9.1')
    expect(sections[1].find('a').exists()).toBe(false)
    expect(w.find('script').exists()).toBe(false)
    expect(w.find('img').exists()).toBe(false)
    expect(sections[1].text()).toContain('<script>alert(1)</script>')
  })

  it('shows placeholders without a status', async () => {
    makeStore(null)
    const w = await mountComponent('@/components/updates/ReleaseNotesModal.vue', { modelValue: true })
    expect(w.text()).toContain('Änderungen nach — bis einschließlich —')
    expect(w.find('[data-testid="release-notes-empty"]').exists()).toBe(true)
  })

  it('shows loading and error states', async () => {
    makeStore(baseStatus())
    store.notesLoading = true
    const w = await mountComponent('@/components/updates/ReleaseNotesModal.vue', { modelValue: true })
    expect(w.find('[data-testid="release-notes-empty"]').exists()).toBe(false)
    store.notesLoading = false
    store.notesError = true
    await flushPromises()
    expect(w.find('[data-testid="release-notes-error"]').exists()).toBe(true)
  })

  it('does not load while closed and forwards close', async () => {
    makeStore(baseStatus())
    const w = await mountComponent('@/components/updates/ReleaseNotesModal.vue', { modelValue: false })
    expect(store.fetchReleaseNotes).not.toHaveBeenCalled()
    await w.setProps({ modelValue: true })
    expect(store.fetchReleaseNotes).toHaveBeenCalledTimes(1)
    await w.find('.modal-close').trigger('click')
    expect(w.emitted('update:modelValue')).toEqual([[false]])
  })
})

// ---------------------------------------------------------------------------
// UpdateSettingsPanel
// ---------------------------------------------------------------------------

describe('UpdateSettingsPanel', () => {
  it('shows a load error with a retry instead of spinning forever', async () => {
    makeStore(null)
    store.statusError = true
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    expect(w.find('[data-testid="update-status-error"]').text()).toContain('Der Update-Status konnte nicht geladen werden.')
    await w.find('[data-testid="update-status-retry"]').trigger('click')
    expect(store.fetchStatus).toHaveBeenCalledTimes(2)
  })

  it('shows a spinner until the status is loaded', async () => {
    makeStore(null)
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    expect(store.fetchStatus).toHaveBeenCalled()
    expect(w.find('[data-testid="update-installed"]').exists()).toBe(false)
  })

  it('shows the full status to admins', async () => {
    makeStore(baseStatus())
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    expect(w.find('[data-testid="update-disabled"]').exists()).toBe(false)
    const select = w.find('[data-testid="update-channel-select"]')
    expect(select.findAll('option').map((o) => o.text())).toEqual(['Stable', 'Pre-Release', 'Nightly'])
    expect(w.find('[data-testid="update-installed"]').text()).toBe('2026.9.0')
    expect(w.find('[data-testid="update-latest"]').text()).toBe('2026.10.0')
    expect(w.find('[data-testid="update-availability"]').text()).toBe('Update verfügbar')
    expect(w.find('[data-testid="update-next-check"]').exists()).toBe(true)
    expect(w.find('[data-testid="update-error"]').exists()).toBe(false)
    expect(w.find('[data-testid="update-check-now"]').exists()).toBe(true)
    expect(w.find('[data-testid="update-open-notes"]').exists()).toBe(true)
    expect(w.text()).toContain('Meldet ausschließlich reguläre Releases.')
  })

  it('shows unknown values and never-checked timestamps', async () => {
    makeStore(baseStatus({ latest_version: null, update_available: null, last_check_at: null, last_success_at: null, next_check_at: null, release_notes_count: 0 }))
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    expect(w.find('[data-testid="update-latest"]').text()).toBe('Unbekannt')
    expect(w.find('[data-testid="update-availability"]').text()).toBe('Unbekannt')
    expect(w.find('[data-testid="update-last-check"]').text()).toBe('noch nie')
    expect(w.find('[data-testid="update-open-notes"]').exists()).toBe(false)
  })

  it('shows an up-to-date installation', async () => {
    makeStore(baseStatus({ update_available: false }))
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    expect(w.find('[data-testid="update-availability"]').text()).toBe('Aktuell')
  })

  it('translates check errors and marks stale results', async () => {
    makeStore(baseStatus({ last_error: 'rate_limited', stale: true }))
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    expect(w.find('[data-testid="update-error"]').text()).toContain('GitHub hat die Anfrage')
    expect(w.find('[data-testid="update-stale"]').exists()).toBe(true)
  })

  it('falls back to a generic text for unknown error codes', async () => {
    makeStore(baseStatus({ last_error: 'something_new' }))
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    expect(w.find('[data-testid="update-error"]').text()).toBe('Die Prüfung ist mit einem internen Fehler fehlgeschlagen.')
    expect(w.find('[data-testid="update-stale"]').exists()).toBe(false)
  })

  it('hides admin actions from other users', async () => {
    isAdmin = false
    makeStore(baseStatus({ channel: 'nightly' }))
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    expect(w.find('[data-testid="update-channel-select"]').exists()).toBe(false)
    expect(w.find('[data-testid="update-channel-text"]').text()).toBe('Nightly')
    expect(w.find('[data-testid="update-check-now"]').exists()).toBe(false)
  })

  it('explains a disabled check and offers no manual check', async () => {
    makeStore(baseStatus({ enabled: false }))
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    expect(w.find('[data-testid="update-disabled"]').exists()).toBe(true)
    expect(w.find('[data-testid="update-check-now"]').exists()).toBe(false)
    expect(w.find('[data-testid="update-next-check"]').exists()).toBe(false)
  })

  it('runs a manual check and reports failures', async () => {
    makeStore(baseStatus())
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    await w.find('[data-testid="update-check-now"]').trigger('click')
    await flushPromises()
    expect(store.checkNow).toHaveBeenCalledTimes(1)
    expect(w.find('[data-testid="update-action-error"]').exists()).toBe(false)

    store.checkNow.mockRejectedValueOnce(new Error('409'))
    await w.find('[data-testid="update-check-now"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="update-action-error"]').text()).toBe('Die Aktion ist fehlgeschlagen. Bitte später erneut versuchen.')
  })

  it('shows a spinner while checking', async () => {
    makeStore(baseStatus())
    store.checking = true
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    expect(w.find('[data-testid="update-check-now"]').attributes('disabled')).toBeDefined()
  })

  it('changes the channel and reports failures', async () => {
    makeStore(baseStatus())
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    await w.find('[data-testid="update-channel-select"]').setValue('pre-release')
    await flushPromises()
    expect(store.setChannel).toHaveBeenCalledWith('pre-release')
    expect(w.text()).toContain('Meldet reguläre Releases und Release Candidates')

    store.setChannel.mockRejectedValueOnce(new Error('403'))
    await w.find('[data-testid="update-channel-select"]').setValue('nightly')
    await flushPromises()
    expect(w.find('[data-testid="update-action-error"]').exists()).toBe(true)
    // The select falls back to the channel the server still has.
    expect(w.find('[data-testid="update-channel-select"]').element.value).toBe('pre-release')
  })

  it('opens the release notes', async () => {
    makeStore(baseStatus())
    const w = await mountComponent('@/components/updates/UpdateSettingsPanel.vue')
    await w.find('[data-testid="update-open-notes"]').trigger('click')
    await flushPromises()
    expect(w.find('.modal-stub').exists()).toBe(true)
    await w.find('.modal-close').trigger('click')
    expect(w.find('.modal-stub').exists()).toBe(false)
  })
})
