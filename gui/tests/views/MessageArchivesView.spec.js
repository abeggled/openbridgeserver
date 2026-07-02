import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const archive = {
  id: 'system',
  name: 'System',
  description: 'Systemmeldungen',
  default_type: 'system',
  color: '#123456',
  retention_max_entries: 100,
  retention_max_age_days: 30,
  entry_count: 2,
  db_path: '/data/archives/messages.sqlite3',
}

const entry = {
  id: 'entry-1',
  archive_id: 'system',
  created_at: '2026-01-01T12:00:00Z',
  title: 'Backup fehlgeschlagen',
  message: 'Auto-Backup konnte nicht erstellt werden.',
  type: 'system',
  severity: 'warning',
  status: 'new',
  source: 'pytest',
}

const api = {
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  delete: vi.fn(),
  clear: vi.fn(),
  integrityCheck: vi.fn(),
  entries: vi.fn(),
  export: vi.fn(),
}

function resetApi() {
  api.list.mockResolvedValue({ data: [archive] })
  api.entries.mockResolvedValue({ data: { items: [entry], total: 1 } })
  api.create.mockResolvedValue({ data: { ...archive, id: 'alarm-archiv', name: 'Alarm Archiv' } })
  api.update.mockResolvedValue({ data: { ...archive, name: 'System aktualisiert' } })
  api.delete.mockResolvedValue({})
  api.clear.mockResolvedValue({})
  api.integrityCheck.mockResolvedValue({ data: { result: 'ok' } })
  api.export.mockResolvedValue({ data: new Blob(['{}'], { type: 'application/jsonl' }) })
}

async function mountView({ isAdmin = true } = {}) {
  vi.doMock('@/api/client', () => ({ messageArchivesApi: api }))
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: isAdmin ? 'admin' : 'viewer', is_admin: isAdmin }
  const { default: MessageArchivesView } = await import('@/views/MessageArchivesView.vue')
  const wrapper = mount(MessageArchivesView, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper, text) {
  const button = wrapper.findAll('button').find(btn => btn.text().includes(text))
  expect(button, `button ${text}`).toBeTruthy()
  return button
}

describe('MessageArchivesView', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    resetApi()
    vi.stubGlobal('confirm', vi.fn(() => true))
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:archive') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
    HTMLAnchorElement.prototype.click = vi.fn()
  })

  afterEach(() => {
    vi.doUnmock('@/api/client')
    vi.unstubAllGlobals()
    document.body.innerHTML = ''
  })

  it('loads archives and filters entries', async () => {
    const wrapper = await mountView()

    expect(api.list).toHaveBeenCalled()
    expect(api.entries).toHaveBeenCalledWith({ archive_id: 'system', limit: 200 })
    expect(wrapper.text()).toContain('Systemmeldungen')
    expect(wrapper.text()).toContain('Backup fehlgeschlagen')
    expect(wrapper.text()).toContain('Warnung')
    expect(wrapper.text()).toContain('Neu')

    const search = wrapper.find('input[placeholder="Titel oder Text suchen …"]')
    await search.setValue('backup')
    const selects = wrapper.findAll('select')
    await selects[0].setValue('warning')
    await selects[1].setValue('new')
    await selects[2].setValue('system')
    await buttonByText(wrapper, 'Aktualisieren').trigger('click')

    expect(api.entries).toHaveBeenLastCalledWith({
      archive_id: 'system',
      limit: 200,
      q: 'backup',
      severity: 'warning',
      status: 'new',
      type: 'system',
    })

    wrapper.unmount()
  })

  it('creates an archive with generated lowercase id and custom default type', async () => {
    const wrapper = await mountView()

    await buttonByText(wrapper, 'Neues Archiv').trigger('click')
    await wrapper.findAll('input')[0].setValue('Alarm Archiv')
    expect(wrapper.findAll('input')[1].element.value).toBe('alarm-archiv')
    await wrapper.find('select').setValue('__custom')
    await flushPromises()
    await wrapper.find('input[placeholder="z.B. maintenance"]').setValue('Maintenance')
    await wrapper.findAll('input')[2].setValue('Beschreibung')
    await wrapper.findAll('input')[5].setValue('25')
    await wrapper.findAll('input')[6].setValue('7')
    await buttonByText(wrapper, 'Speichern').trigger('click')
    await flushPromises()

    expect(api.create).toHaveBeenCalledWith({
      id: 'alarm-archiv',
      name: 'Alarm Archiv',
      description: 'Beschreibung',
      tags: [],
      default_type: 'maintenance',
      color: '#3b82f6',
      retention_max_entries: 25,
      retention_max_age_days: 7,
    })

    wrapper.unmount()
  })

  it('edits, exports, clears, deletes and checks integrity', async () => {
    const wrapper = await mountView()

    await buttonByText(wrapper, 'Bearbeiten').trigger('click')
    await wrapper.findAll('input')[0].setValue('System aktualisiert')
    await buttonByText(wrapper, 'Speichern').trigger('click')
    await flushPromises()
    expect(api.update).toHaveBeenCalledWith('system', expect.objectContaining({ name: 'System aktualisiert' }))

    await buttonByText(wrapper, 'JSONL exportieren').trigger('click')
    expect(api.export).toHaveBeenCalledWith('system', 'jsonl')
    expect(URL.createObjectURL).toHaveBeenCalled()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:archive')

    await buttonByText(wrapper, 'Leeren').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalledWith('2 Einträge endgültig aus diesem Archiv löschen?')
    expect(api.clear).toHaveBeenCalledWith('system', true)

    await buttonByText(wrapper, 'Integrität prüfen').trigger('click')
    await flushPromises()
    expect(api.integrityCheck).toHaveBeenCalled()
    expect(wrapper.text()).toContain('Integritätsprüfung: ok')

    await buttonByText(wrapper, 'Löschen').trigger('click')
    await flushPromises()
    expect(api.delete).toHaveBeenCalledWith('system', true)

    wrapper.unmount()
  })

  it('hides admin archive controls from regular users', async () => {
    const wrapper = await mountView({ isAdmin: false })

    expect(wrapper.text()).not.toContain('Neues Archiv')
    expect(wrapper.text()).not.toContain('Integrität prüfen')
    expect(wrapper.text()).not.toContain('Bearbeiten')
    expect(wrapper.text()).not.toContain('JSONL exportieren')
    expect(wrapper.text()).not.toContain('CSV exportieren')
    expect(wrapper.text()).not.toContain('Leeren')
    expect(wrapper.text()).not.toContain('Löschen')
    expect(wrapper.text()).toContain('Backup fehlgeschlagen')

    wrapper.unmount()
  })
})
