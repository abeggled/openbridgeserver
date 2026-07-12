import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()

vi.mock('@/api/client', () => ({ default: { get } }))

describe('logicRunAuthzApi', () => {
  beforeEach(() => get.mockReset())

  it('requests current-principal run preflight for the selected graph', async () => {
    get.mockResolvedValue({ data: { graph_id: 'graph/a' } })
    const { logicRunAuthzApi } = await import('@/api/logicAuthz')

    await logicRunAuthzApi.preflight('graph/a')

    expect(get).toHaveBeenCalledWith('/logic/graphs/graph/a/run-preflight')
  })
})
