/**
 * Tests for ExportDialog.vue (#427) — CSV/TSV export modal triggered from
 * the monitor topbar. Verifies the form state, settings hydration, and
 * the API call sequence on "Exportieren".
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

let exportMultiCsv
let countExportRows
let getExportSettings
let putExportSettings

beforeEach(() => {
  exportMultiCsv = vi.fn().mockResolvedValue({
    data: new Blob(['ts,value\n2026-05-12T10:00:00Z,1\n']),
    headers: { 'content-disposition': 'attachment; filename="ringbuffer_export_20260512.csv"' },
  })
  countExportRows = vi.fn().mockResolvedValue({ data: { row_count: 42 } })
  getExportSettings = vi.fn().mockResolvedValue({
    data: { format: 'csv', encoding: 'utf8', include_unit: true, include_matched_set_ids: false },
  })
  putExportSettings = vi.fn().mockResolvedValue({ data: {} })

  vi.doMock('@/api/client', () => ({
    ringbufferApi: { exportMultiCsv, countExportRows, getExportSettings, putExportSettings },
  }))

  // Stub Modal so v-model works and the slot renders even when soft-backdrop logic isn't loaded
  vi.doMock('@/components/ui/Modal.vue', () => ({
    default: {
      name: 'Modal',
      props: ['modelValue', 'title', 'softBackdrop', 'maxWidth'],
      emits: ['update:modelValue'],
      template: `
        <div v-if="modelValue" data-testid="modal-stub">
          <slot />
          <div data-testid="modal-footer-slot"><slot name="footer" /></div>
        </div>
      `,
    },
  }))
  vi.doMock('@/components/ui/Spinner.vue', () => ({
    default: { name: 'Spinner', template: '<span />' },
  }))

  // URL/Blob/Document polyfills are present in jsdom; spy createObjectURL to avoid noise
  if (!URL.createObjectURL) URL.createObjectURL = vi.fn(() => 'blob:mock')
  else vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock')
  if (!URL.revokeObjectURL) URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/components/ui/Modal.vue')
  vi.doUnmock('@/components/ui/Spinner.vue')
  vi.resetModules()
})

async function mountDialog(props = {}) {
  const mod = await import('@/views/ringbuffer/ExportDialog.vue')
  return mount(mod.default, {
    props: { modelValue: true, setIds: [], time: null, ...props },
    attachTo: document.body,
  })
}

describe('ExportDialog', () => {
  it('hydrates form from getExportSettings on open', async () => {
    getExportSettings.mockResolvedValueOnce({
      data: { format: 'tsv', encoding: 'utf8-bom', include_unit: false, include_matched_set_ids: true },
    })
    const wrapper = await mountDialog()
    await flushPromises()
    expect(getExportSettings).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="export-format-tsv"]').element.checked).toBe(true)
    expect(wrapper.find('[data-testid="export-encoding"]').element.value).toBe('utf8-bom')
    expect(wrapper.find('[data-testid="export-include-unit"]').element.checked).toBe(false)
    expect(wrapper.find('[data-testid="export-include-matched"]').element.checked).toBe(true)
    wrapper.unmount()
  })

  it('falls back to defaults when getExportSettings fails', async () => {
    getExportSettings.mockRejectedValueOnce(new Error('boom'))
    const wrapper = await mountDialog()
    await flushPromises()
    expect(wrapper.find('[data-testid="export-format-csv"]').element.checked).toBe(true)
    expect(wrapper.find('[data-testid="export-encoding"]').element.value).toBe('utf8')
    wrapper.unmount()
  })

  it('persists settings AND posts the export with the current form state', async () => {
    const wrapper = await mountDialog({ setIds: ['set-a', 'set-b'], time: { from: '2026-05-01T00:00:00Z' } })
    await flushPromises()

    await wrapper.find('[data-testid="export-format-tsv"]').setValue(true)
    await wrapper.find('[data-testid="export-include-matched"]').setValue(true)
    await wrapper.find('[data-testid="btn-export-go"]').trigger('click')
    await flushPromises()

    expect(putExportSettings).toHaveBeenCalledWith(
      expect.objectContaining({ format: 'tsv', include_matched_set_ids: true }),
    )
    expect(exportMultiCsv).toHaveBeenCalledWith(
      expect.objectContaining({
        set_ids: ['set-a', 'set-b'],
        time: { from: '2026-05-01T00:00:00Z' },
        format: 'tsv',
        include_matched_set_ids: true,
      }),
    )
    wrapper.unmount()
  })

  it('emits update:modelValue(false) after a successful export', async () => {
    const wrapper = await mountDialog()
    await flushPromises()
    await wrapper.find('[data-testid="btn-export-go"]').trigger('click')
    await flushPromises()
    const events = wrapper.emitted('update:modelValue') || []
    expect(events.flat()).toContain(false)
    wrapper.unmount()
  })

  it('shows an error message and stays open when the export fails', async () => {
    exportMultiCsv.mockRejectedValueOnce({ response: { data: { detail: 'too many rows' } } })
    const wrapper = await mountDialog()
    await flushPromises()
    await wrapper.find('[data-testid="btn-export-go"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="export-error"]').text()).toContain('too many rows')
    wrapper.unmount()
  })

  it('cancel button closes the modal without calling the export', async () => {
    const wrapper = await mountDialog()
    await flushPromises()
    await wrapper.find('[data-testid="btn-export-cancel"]').trigger('click')
    expect(exportMultiCsv).not.toHaveBeenCalled()
    const events = wrapper.emitted('update:modelValue') || []
    expect(events.flat()).toContain(false)
    wrapper.unmount()
  })

  // -------------------------------------------------------------------------
  // Preflight count warning (>1000 rows)
  // -------------------------------------------------------------------------

  it('exports directly when the preflight count is at or below the threshold', async () => {
    countExportRows.mockResolvedValueOnce({ data: { row_count: 1000 } })
    const wrapper = await mountDialog({ setIds: ['set-a'] })
    await flushPromises()

    await wrapper.find('[data-testid="btn-export-go"]').trigger('click')
    await flushPromises()

    expect(countExportRows).toHaveBeenCalledTimes(1)
    expect(countExportRows).toHaveBeenCalledWith({ set_ids: ['set-a'], time: null })
    expect(exportMultiCsv).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="export-warning"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows a warning with the row count and does NOT export on the first click when above threshold', async () => {
    countExportRows.mockResolvedValueOnce({ data: { row_count: 5234 } })
    const wrapper = await mountDialog({ setIds: ['set-a'] })
    await flushPromises()

    await wrapper.find('[data-testid="btn-export-go"]').trigger('click')
    await flushPromises()

    expect(exportMultiCsv).not.toHaveBeenCalled()
    const warning = wrapper.find('[data-testid="export-warning"]')
    expect(warning.exists()).toBe(true)
    expect(warning.text()).toContain('5.234')
    // Button label flips to the confirm wording
    expect(wrapper.find('[data-testid="btn-export-go"]').text()).toContain('Trotzdem exportieren')
    wrapper.unmount()
  })

  it('exports without re-counting when the user confirms the warning', async () => {
    countExportRows.mockResolvedValueOnce({ data: { row_count: 5000 } })
    const wrapper = await mountDialog({ setIds: ['set-a'] })
    await flushPromises()

    // First click → warning
    await wrapper.find('[data-testid="btn-export-go"]').trigger('click')
    await flushPromises()
    expect(exportMultiCsv).not.toHaveBeenCalled()

    // Second click → actual export, no extra count call
    await wrapper.find('[data-testid="btn-export-go"]').trigger('click')
    await flushPromises()
    expect(countExportRows).toHaveBeenCalledTimes(1)
    expect(exportMultiCsv).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('preserves the warning when the parent passes a new setIds reference with identical content', async () => {
    // Regression: RingBufferView re-renders on every live WS entry, and its
    // bindings `:set-ids="activeTopbarSetIds()"` / `:time="timeFilterToPayload(timeFilter)"`
    // return new array/object references each time. A naive deep-watch on
    // [setIds, time] fires on every parent re-render (Vue's hasChanged uses
    // Object.is on the top-level snapshot) and wipes pendingRowCount before
    // the user can confirm the warning.
    countExportRows.mockResolvedValueOnce({ data: { row_count: 5234 } })
    const wrapper = await mountDialog({ setIds: ['set-a'], time: { from: '2026-05-01T00:00:00Z' } })
    await flushPromises()

    await wrapper.find('[data-testid="btn-export-go"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="export-warning"]').exists()).toBe(true)

    // Simulate a parent re-render: identical content, new references.
    await wrapper.setProps({ setIds: ['set-a'], time: { from: '2026-05-01T00:00:00Z' } })
    await flushPromises()
    expect(wrapper.find('[data-testid="export-warning"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="btn-export-go"]').text()).toContain('Trotzdem exportieren')

    // Real content change → warning is invalidated.
    await wrapper.setProps({ setIds: ['set-b'], time: { from: '2026-05-01T00:00:00Z' } })
    await flushPromises()
    expect(wrapper.find('[data-testid="export-warning"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('cancel button aborts the export even while the warning is shown', async () => {
    countExportRows.mockResolvedValueOnce({ data: { row_count: 9999 } })
    const wrapper = await mountDialog({ setIds: ['set-a'] })
    await flushPromises()

    await wrapper.find('[data-testid="btn-export-go"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="btn-export-cancel"]').trigger('click')

    expect(exportMultiCsv).not.toHaveBeenCalled()
    const events = wrapper.emitted('update:modelValue') || []
    expect(events.flat()).toContain(false)
    wrapper.unmount()
  })

  it('surfaces preflight-count failures via the error slot and skips the export', async () => {
    countExportRows.mockRejectedValueOnce({ response: { data: { detail: 'count failed' } } })
    const wrapper = await mountDialog({ setIds: ['set-a'] })
    await flushPromises()

    await wrapper.find('[data-testid="btn-export-go"]').trigger('click')
    await flushPromises()

    expect(exportMultiCsv).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="export-error"]').text()).toContain('count failed')
    wrapper.unmount()
  })
})
