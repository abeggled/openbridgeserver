import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

let listGA

beforeEach(() => {
  vi.resetModules()
  listGA = vi.fn()
  vi.doMock('@/api/client', () => ({ knxprojApi: { listGA } }))
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function store() {
  const { useKnxProjectStore } = await import('@/stores/knxProject')
  return useKnxProjectStore()
}

const page = (style, conflicts) => ({ data: { total: 1, items: [], group_address_style: style, merge_conflicts: conflicts } })

describe('useKnxProjectStore', () => {
  it('starts three-level and takes style and merge notes from the group address API', async () => {
    const conflict = { address: '1/0/234', spelling: '1/234', field: 'name', kept: 'a', dropped: 'b' }
    listGA.mockResolvedValue(page('TwoLevel', [conflict]))
    const knx = await store()
    expect(knx.groupAddressStyle).toBe('ThreeLevel')

    await knx.load()
    expect(listGA).toHaveBeenCalledWith({ size: 1 })
    expect(knx.groupAddressStyle).toBe('TwoLevel')
    expect(knx.mergeConflicts).toEqual([conflict])
  })

  it('loads once, shares a running request, and reloads on force', async () => {
    listGA.mockResolvedValue(page('Free'))
    const knx = await store()
    await Promise.all([knx.load(), knx.load()])
    await knx.load()
    expect(listGA).toHaveBeenCalledTimes(1)
    expect(knx.mergeConflicts).toEqual([])

    listGA.mockResolvedValue(page('TwoLevel'))
    await knx.load({ force: true })
    expect(listGA).toHaveBeenCalledTimes(2)
    expect(knx.groupAddressStyle).toBe('TwoLevel')
  })

  it('a forced reload wins over an older answer that arrives later', async () => {
    let answerOld
    listGA.mockReturnValueOnce(new Promise(resolve => { answerOld = resolve }))
    listGA.mockResolvedValueOnce(page('Free'))
    const knx = await store()
    const old = knx.load()
    await knx.load({ force: true })
    answerOld(page('TwoLevel'))
    await old
    expect(knx.groupAddressStyle).toBe('Free')
  })

  it('falls back to three-level for an unknown style and keeps the style when the API fails', async () => {
    listGA.mockResolvedValueOnce(page('FourLevel'))
    const knx = await store()
    await knx.load()
    expect(knx.groupAddressStyle).toBe('ThreeLevel')

    listGA.mockResolvedValueOnce(page('TwoLevel'))
    await knx.load({ force: true })
    listGA.mockRejectedValueOnce(new Error('offline'))
    await knx.load({ force: true })
    expect(knx.groupAddressStyle).toBe('TwoLevel')
  })

  it('tries again after a failed first load', async () => {
    listGA.mockRejectedValueOnce(new Error('offline'))
    listGA.mockResolvedValueOnce(page('Free'))
    const knx = await store()
    await knx.load()
    await knx.load()
    expect(listGA).toHaveBeenCalledTimes(2)
    expect(knx.groupAddressStyle).toBe('Free')
  })
})

describe('useKnxProjectStore — style unavailable (#1296, P4b round 2)', () => {
  it('flags a failed load, a missing and an unknown style, and clears the flag on success', async () => {
    const knx = await store()
    expect(knx.styleUnavailable).toBe(false)

    listGA.mockRejectedValueOnce(new Error('offline'))
    await knx.load()
    expect(knx.styleUnavailable).toBe(true)

    listGA.mockResolvedValueOnce({ data: { total: 0, items: [] } })
    await knx.load({ force: true })
    expect(knx.styleUnavailable).toBe(true)

    listGA.mockResolvedValueOnce(page('FourLevel'))
    await knx.load({ force: true })
    expect(knx.styleUnavailable).toBe(true)

    listGA.mockResolvedValueOnce(page('TwoLevel'))
    await knx.load({ force: true })
    expect(knx.groupAddressStyle).toBe('TwoLevel')
    expect(knx.styleUnavailable).toBe(false)
  })
})
