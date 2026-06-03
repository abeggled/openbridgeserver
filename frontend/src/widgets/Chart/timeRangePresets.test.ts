import { describe, expect, it } from 'vitest'
import {
  DAY_MS,
  DEFAULT_HISTORY_QUERY_LIMIT,
  historyRequestPlanForRange,
} from './timeRangePresets'

describe('historyRequestPlanForRange', () => {
  it('keeps raw history queries for ranges up to one day', () => {
    const to = new Date('2026-06-03T12:00:00.000Z')
    const from = new Date('2026-06-02T12:00:00.000Z')

    expect(historyRequestPlanForRange(from, to)).toEqual({
      mode: 'raw',
      limit: DEFAULT_HISTORY_QUERY_LIMIT,
    })
  })

  it('keeps the exact one-day boundary as a raw query', () => {
    const to = new Date('2026-06-03T12:00:00.000Z')
    const from = new Date(to.getTime() - DAY_MS)

    expect(historyRequestPlanForRange(from, to)).toEqual({
      mode: 'raw',
      limit: DEFAULT_HISTORY_QUERY_LIMIT,
    })
  })

  it('falls back to a raw query for invalid dates', () => {
    expect(historyRequestPlanForRange(new Date('invalid'), new Date('2026-06-03T12:00:00.000Z'))).toEqual({
      mode: 'raw',
      limit: DEFAULT_HISTORY_QUERY_LIMIT,
    })
  })

  it('compresses two-day chart ranges into one-hour buckets', () => {
    const to = new Date('2026-06-03T12:00:00.000Z')
    const from = new Date('2026-06-01T12:00:00.000Z')

    expect(historyRequestPlanForRange(from, to)).toEqual({
      mode: 'aggregate',
      fn: 'avg',
      interval: '1h',
    })
  })

  it('compresses seven-day chart ranges into one-hour buckets', () => {
    const to = new Date('2026-06-03T12:00:00.000Z')
    const from = new Date('2026-05-27T12:00:00.000Z')

    expect(historyRequestPlanForRange(from, to)).toEqual({
      mode: 'aggregate',
      fn: 'avg',
      interval: '1h',
    })
  })

  it('compresses 30-day chart ranges into six-hour buckets', () => {
    const to = new Date('2026-06-03T12:00:00.000Z')
    const from = new Date('2026-05-04T12:00:00.000Z')

    expect(historyRequestPlanForRange(from, to)).toEqual({
      mode: 'aggregate',
      fn: 'avg',
      interval: '6h',
    })
  })

  it('compresses 90-day chart ranges into 12-hour buckets', () => {
    const to = new Date('2026-06-03T12:00:00.000Z')
    const from = new Date('2026-03-05T12:00:00.000Z')

    expect(historyRequestPlanForRange(from, to)).toEqual({
      mode: 'aggregate',
      fn: 'avg',
      interval: '12h',
    })
  })
})
