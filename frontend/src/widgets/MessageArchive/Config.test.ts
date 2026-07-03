// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MessageArchiveConfig from './Config.vue'

vi.mock('@/api/client', () => ({
  messageArchives: {
    list: vi.fn().mockResolvedValue([
      {
        id: 'security',
        name: 'security',
        description: '',
        tags: [],
        default_type: null,
        color: '#3b82f6',
        retention_max_entries: null,
        retention_max_age_days: null,
        created_at: '2026-01-01T10:00:00+00:00',
        updated_at: '2026-01-01T10:00:00+00:00',
        entry_count: 0,
        oldest_entry_at: null,
        newest_entry_at: null,
        db_status: 'ok',
        db_path: '',
      },
    ]),
    entries: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 1000, offset: 0 }),
  },
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => `${key}${params?.count ?? params?.n ?? ''}`,
  }),
}))

describe('MessageArchive Config.vue', () => {
  it('preserves singular archive_id filters when emitting updates', async () => {
    const wrapper = mount(MessageArchiveConfig, {
      props: {
        modelValue: {
          archive_id: 'security',
          limit: 25,
        },
      },
      global: {
        mocks: {
          $t: (key: string) => key,
        },
      },
    })
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()

    await wrapper.find('input[type="number"]').setValue('50')

    const updates = wrapper.emitted('update:modelValue') ?? []
    expect(updates[updates.length - 1][0]).toMatchObject({
      archive_ids: ['security'],
      limit: 50,
    })
  })
})
