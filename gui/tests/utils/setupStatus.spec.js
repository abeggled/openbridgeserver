/**
 * Cached first-run setup state (#1229) — consulted by the router on every
 * navigation, so it must ask the backend exactly once.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function loadModule(status) {
  vi.doMock('@/api/client', () => ({ setupApi: { status } }))
  return import('@/utils/setupStatus')
}

describe('fetchSetupRequired', () => {
  it('reports an installation that still has to be claimed', async () => {
    const status = vi.fn().mockResolvedValue({ data: { setup_required: true } })
    const { fetchSetupRequired } = await loadModule(status)

    expect(await fetchSetupRequired()).toBe(true)
  })

  it('reports a configured installation', async () => {
    const status = vi.fn().mockResolvedValue({ data: { setup_required: false } })
    const { fetchSetupRequired } = await loadModule(status)

    expect(await fetchSetupRequired()).toBe(false)
  })

  it('asks the backend only once', async () => {
    const status = vi.fn().mockResolvedValue({ data: { setup_required: true } })
    const { fetchSetupRequired } = await loadModule(status)

    await fetchSetupRequired()
    await fetchSetupRequired()

    expect(status).toHaveBeenCalledTimes(1)
  })

  it('falls back to normal operation when the route cannot be reached', async () => {
    const status = vi.fn().mockRejectedValue(new Error('offline'))
    const { fetchSetupRequired } = await loadModule(status)

    expect(await fetchSetupRequired()).toBe(false)
  })

  it('gives up on a status call that never settles', async () => {
    // The router guard awaits this before it resolves any route — a stalled
    // request must not leave the app on a blank page for good.
    vi.useFakeTimers()
    const status = vi.fn(() => new Promise(() => {}))
    const { fetchSetupRequired } = await loadModule(status)

    const answer = fetchSetupRequired()
    await vi.advanceTimersByTimeAsync(10000)

    await expect(answer).resolves.toBe(false)
    vi.useRealTimers()
  })

  it('treats a response without the field as configured', async () => {
    const status = vi.fn().mockResolvedValue({ data: {} })
    const { fetchSetupRequired } = await loadModule(status)

    expect(await fetchSetupRequired()).toBe(false)
  })
})

describe('cache control', () => {
  it('markSetupComplete answers without asking again', async () => {
    const status = vi.fn().mockResolvedValue({ data: { setup_required: true } })
    const { fetchSetupRequired, markSetupComplete } = await loadModule(status)

    markSetupComplete()

    expect(await fetchSetupRequired()).toBe(false)
    expect(status).not.toHaveBeenCalled()
  })

  it('resetSetupStatusCache forces a fresh answer', async () => {
    const status = vi.fn().mockResolvedValue({ data: { setup_required: true } })
    const { fetchSetupRequired, resetSetupStatusCache } = await loadModule(status)

    await fetchSetupRequired()
    resetSetupStatusCache()
    await fetchSetupRequired()

    expect(status).toHaveBeenCalledTimes(2)
  })
})
