// Shared write queue of the logic store (issue #1333).
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', () => ({ logicApi: {} }))

import { useLogicStore } from '@/stores/logic'

beforeEach(() => setActivePinia(createPinia()))

function held() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { task: vi.fn(() => promise), resolve, reject }
}

describe('logic store write queue', () => {
  it('runs writes strictly one after another, also after a failed one', async () => {
    const store = useLogicStore()
    const first = held()
    const second = vi.fn().mockResolvedValue('second')

    const failing = store.enqueueWrite(first.task)
    const next = store.enqueueWrite(second)
    await flushPromises()
    expect(first.task).toHaveBeenCalled()
    expect(second).not.toHaveBeenCalled()

    first.reject(new Error('down'))
    await expect(failing).rejects.toThrow('down')
    await expect(next).resolves.toBe('second')
  })

  it('tracks the latest save per sheet until it settles', async () => {
    const store = useLogicStore()
    const older = held()
    const newer = held()

    store.enqueueWrite(older.task, 'g1')
    store.enqueueWrite(newer.task, 'g1')
    // Toggle/rename writes carry no sheet to track.
    store.enqueueWrite(() => 'patched')
    expect(store.pendingSave('g2')).toBeUndefined()
    // Pinia wraps every action result in a new promise, so compare outcomes.
    const tracked = store.pendingSave('g1')

    // The older save settling must not drop the newer one's entry.
    older.resolve('older')
    await flushPromises()
    const stillTracked = store.pendingSave('g1')
    expect(stillTracked).toBeDefined()

    newer.resolve('newer')
    await expect(tracked).resolves.toBe('newer')
    await expect(stillTracked).resolves.toBe('newer')
    await flushPromises()
    expect(store.pendingSave('g1')).toBeUndefined()
  })
})
