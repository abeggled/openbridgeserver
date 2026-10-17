import { describe, expect, it } from 'vitest'
import {
  hasPushoverTarget,
  normalizeFormat,
  sendsNotification,
  normalizePriority,
  normalizePushoverOptions,
  pushoverOptionErrors,
} from '@/utils/pushoverOptions'

const E = 'adapters.pushoverOptions.errors.'

describe('pushoverOptions', () => {
  it('detects Pushover targets', () => {
    expect(hasPushoverTarget([{ provider: 'telegram' }, { provider: 'pushover' }])).toBe(true)
    expect(hasPushoverTarget([{ provider: 'telegram' }, null])).toBe(false)
    expect(hasPushoverTarget('invalid')).toBe(false)
  })

  it('normalizes priorities', () => {
    expect(normalizePriority('')).toBeNull()
    expect(normalizePriority(null)).toBeNull()
    expect(normalizePriority('2')).toBe(2)
    expect(normalizePriority(-1)).toBe(-1)
    expect(normalizePriority('1.5')).toBeNull()
  })

  it('keeps only set options', () => {
    expect(normalizePushoverOptions(null)).toEqual({})
    expect(normalizePushoverOptions({ image_url: '  ', url: ' https://obs.example ', ttl: '', retry: '60', format: 'text' }, 2))
      .toEqual({ url: 'https://obs.example', retry: 60 })
    expect(normalizePushoverOptions({ format: 'html', sound: 'siren', timestamp: 1700000000 }))
      .toEqual({ format: 'html', sound: 'siren', timestamp: 1700000000 })
    expect(normalizePushoverOptions({ format: 'markdown' })).toEqual({})
  })

  it('mirrors the backend length limits', () => {
    expect(pushoverOptionErrors({ image_url: 'x'.repeat(2049), url: 'x'.repeat(513), url_title: 'x'.repeat(101), sound: 'x'.repeat(33) }, null)).toEqual({
      image_url: `${E}tooLong`, url: `${E}tooLong`, url_title: `${E}tooLong`, sound: `${E}tooLong`,
    })
    expect(pushoverOptionErrors({ url: 'x'.repeat(512), url_title: 'x'.repeat(100), sound: 'x'.repeat(32) }, null)).toEqual({})
  })

  it('normalizes format case and knows the sending strategies', () => {
    expect(normalizePushoverOptions({ format: 'HTML' })).toEqual({ format: 'html' })
    expect(normalizeFormat(' Monospace ')).toBe('monospace')
    expect(normalizeFormat(null)).toBe('text')
    expect(sendsNotification(undefined)).toBe(true)
    expect(sendsNotification('send_and_archive')).toBe(true)
    expect(sendsNotification('archive_only')).toBe(false)
  })

  it('drops hidden retry and expire below emergency priority', () => {
    expect(normalizePushoverOptions({ retry: 10, expire: 99999, ttl: 5 }, 1)).toEqual({ ttl: 5 })
    expect(pushoverOptionErrors({ retry: 10, expire: 99999 }, 1)).toEqual({})
  })

  it('accepts a valid emergency configuration', () => {
    expect(pushoverOptionErrors({ retry: 30, expire: 10800, ttl: 1, url: 'u', url_title: 't', sound: 'my_sound-1' }, 2)).toEqual({})
  })

  it('requires retry and expire for emergency priority', () => {
    expect(pushoverOptionErrors({}, '2')).toEqual({ retry: `${E}retryRequired`, expire: `${E}expireRequired` })
    expect(pushoverOptionErrors({}, 1)).toEqual({})
  })

  it('validates ranges, integers and combinations', () => {
    expect(pushoverOptionErrors({ retry: 29, expire: 10801, ttl: 0, timestamp: -1 }, 2)).toEqual({
      retry: `${E}retryMin`, expire: `${E}expireRange`, ttl: `${E}positive`, timestamp: `${E}positive`,
    })
    expect(pushoverOptionErrors({ retry: 30.5, expire: 'x', ttl: 1.2, timestamp: 2.5 }, 2)).toEqual({
      retry: `${E}integer`, expire: `${E}integer`, ttl: `${E}integer`, timestamp: `${E}integer`,
    })
    expect(pushoverOptionErrors({ retry: 30, expire: 0 }, 2)).toEqual({ expire: `${E}expireRange` })
    expect(pushoverOptionErrors({ url_title: 'Open' }, null)).toEqual({ url_title: `${E}urlTitleWithoutUrl` })
    expect(pushoverOptionErrors({ url_title: 'Open' }, null, { urlFromInput: true })).toEqual({})
    expect(pushoverOptionErrors({ sound: 'my sound' }, null)).toEqual({ sound: `${E}soundInvalid` })
  })
})
