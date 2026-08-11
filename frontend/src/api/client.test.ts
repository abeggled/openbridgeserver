// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiRequestError, messageArchives, visu } from './client'

describe('structured API errors', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('preserves the stable code and actionable details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: 'visu_target_audience_datapoints_denied',
        username: 'alice',
        datapoint_ids: ['blocked.dp'],
      },
    }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })))

    const error = await visu.updateNode('node-1', { access: 'user', usernames: ['alice'] }).catch(value => value)

    expect(error).toBeInstanceOf(ApiRequestError)
    expect(error).toMatchObject({
      status: 403,
      code: 'visu_target_audience_datapoints_denied',
      details: {
        username: 'alice',
        datapoint_ids: ['blocked.dp'],
      },
    })
  })

  it('preserves the HTTP status for plain API error details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: 'Forbidden',
    }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })))

    const error = await messageArchives.markRead('archive-1', 'entry-1').catch(value => value)

    expect(error).toBeInstanceOf(ApiRequestError)
    expect(error).toMatchObject({
      message: 'Forbidden',
      status: 403,
    })
  })
})
