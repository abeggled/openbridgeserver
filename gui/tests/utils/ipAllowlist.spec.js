import { describe, it, expect } from 'vitest'
import {
  classifyEntry,
  isAddressCovered,
  normalizeEntries,
  isLoopbackHost,
  parseIpv4,
  parseIpv4Entry,
} from '@/utils/ipAllowlist'

describe('parseIpv4', () => {
  it.each([
    ['0.0.0.0', 0],
    ['127.0.0.1', 2130706433],
    ['255.255.255.255', 4294967295],
    ['10.38.111.21', 170290965],
  ])('parses %s', (text, expected) => {
    expect(parseIpv4(text)).toBe(expected)
  })

  it.each(['', '  ', '1.2.3', '1.2.3.4.5', '256.0.0.1', 'abc', '1.2.3.-1', null, undefined, 'fd00::1', '010.0.0.1', '1.02.3.4', '1.2.3.00'])(
    'rejects %s',
    (text) => {
      expect(parseIpv4(text)).toBeNull()
    },
  )
})

describe('parseIpv4Entry', () => {
  it('treats a bare address as a host route', () => {
    expect(parseIpv4Entry('192.168.1.5')).toEqual({ address: parseIpv4('192.168.1.5'), prefixLength: 32 })
  })

  it('parses a prefix length', () => {
    expect(parseIpv4Entry('10.0.0.0/8')).toEqual({ address: parseIpv4('10.0.0.0'), prefixLength: 8 })
    expect(parseIpv4Entry(' 10.0.0.0/0 ')).toEqual({ address: parseIpv4('10.0.0.0'), prefixLength: 0 })
  })

  it.each(['', '10.0.0.0/33', '10.0.0.0/x', '10.0.0.0/', '10.0.0.0/-1', 'fd00::/8', '10.0.0.0/999', null, undefined])(
    'rejects %s',
    (text) => {
      expect(parseIpv4Entry(text)).toBeNull()
    },
  )
})

describe('classifyEntry', () => {
  it.each([
    ['10.0.0.0/8', 'valid'],
    ['192.168.1.5', 'valid'],
    ['fd00::/8', 'unchecked'],
    ['::1', 'unchecked'],
    ['10.0.0.0/33', 'invalid'],
    ['999.1.1.1', 'invalid'],
    ['nope', 'invalid'],
    ['', 'invalid'],
    [null, 'invalid'],
    [undefined, 'invalid'],
  ])('classifies %s as %s', (text, expected) => {
    expect(classifyEntry(text)).toBe(expected)
  })
})

describe('isLoopbackHost', () => {
  it.each(['localhost', 'LOCALHOST', '127.0.0.1', '127.1.2.3', '::1', '[::1]'])('accepts %s', (host) => {
    expect(isLoopbackHost(host)).toBe(true)
  })

  it.each(['10.38.111.21', 'obs.local', '', null])('rejects %s', (host) => {
    expect(isLoopbackHost(host)).toBe(false)
  })
})

describe('isAddressCovered', () => {
  it('covers everything when the allowlist is empty', () => {
    expect(isAddressCovered('127.0.0.1', [])).toBe(true)
    expect(isAddressCovered('obs.local', undefined)).toBe(true)
    expect(isAddressCovered('127.0.0.1', ['', '   '])).toBe(true)
  })

  it('decides IPv4 membership', () => {
    expect(isAddressCovered('10.38.111.21', ['10.38.0.0/16'])).toBe(true)
    expect(isAddressCovered('10.39.0.1', ['10.38.0.0/16'])).toBe(false)
    expect(isAddressCovered('192.168.1.5', ['10.0.0.0/8', '192.168.1.5/32'])).toBe(true)
    expect(isAddressCovered('1.2.3.4', ['0.0.0.0/0'])).toBe(true)
  })

  it('reproduces the reported case: loopback against a LAN allowlist', () => {
    expect(isAddressCovered('localhost', ['10.38.0.0/16'])).toBe(false)
    expect(isAddressCovered('127.0.0.1', ['10.38.0.0/16'])).toBe(false)
    expect(isAddressCovered('10.38.111.21', ['10.38.0.0/16'])).toBe(true)
  })

  it('treats localhost as covered when loopback is listed', () => {
    expect(isAddressCovered('localhost', ['127.0.0.0/8'])).toBe(true)
    expect(isAddressCovered('localhost', ['127.0.0.1'])).toBe(true)
  })

  it('gives no verdict for a hostname it cannot resolve', () => {
    expect(isAddressCovered('obs.local', ['10.38.0.0/16'])).toBeNull()
    expect(isAddressCovered('', ['10.38.0.0/16'])).toBeNull()
    expect(isAddressCovered(null, ['10.38.0.0/16'])).toBeNull()
  })

  it('gives no verdict when an entry cannot be judged', () => {
    expect(isAddressCovered('10.39.0.1', ['fd00::/8'])).toBeNull()
    // …but a positive IPv4 match still wins over an unjudgeable entry.
    expect(isAddressCovered('10.38.0.1', ['fd00::/8', '10.38.0.0/16'])).toBe(true)
  })

  it('recognises an explicitly listed IPv6 loopback for a localhost origin', () => {
    expect(isAddressCovered('::1', ['::1'])).toBe(true)
    expect(isAddressCovered('[::1]', ['::1/128'])).toBe(true)
    expect(isAddressCovered('::1', ['fd00::/8'])).toBeNull()
  })
})

describe('normalizeEntries', () => {
  it('keeps a list and stringifies its items', () => {
    expect(normalizeEntries(['10.0.0.0/8', 5])).toEqual(['10.0.0.0/8', '5'])
  })

  it('splits the legacy comma-separated string', () => {
    expect(normalizeEntries('10.0.0.0/8, 192.168.1.4;fd00::1\n127.0.0.1')).toEqual([
      '10.0.0.0/8',
      '192.168.1.4',
      'fd00::1',
      '127.0.0.1',
    ])
  })

  it.each([null, undefined, ''])('turns %s into an empty list', (value) => {
    expect(normalizeEntries(value)).toEqual([])
  })
})

describe('legacy string allowlists', () => {
  it('are judged like a list by isAddressCovered', () => {
    expect(isAddressCovered('10.1.2.3', '10.0.0.0/8,192.168.1.4')).toBe(true)
    expect(isAddressCovered('172.16.0.1', '10.0.0.0/8,192.168.1.4')).toBe(false)
  })
})

describe('leading-zero IPv4 spellings', () => {
  it('are classified invalid, as the backend schema rejects them', () => {
    expect(classifyEntry('010.0.0.1')).toBe('invalid')
    expect(classifyEntry('10.0.0.0/8')).toBe('valid')
    expect(parseIpv4Entry('010.0.0.0/8')).toBeNull()
  })
})

describe('a literal IPv6 loopback host', () => {
  it.each(['::1', '[::1]'])('%s is not covered by an IPv4-only allowlist', (host) => {
    expect(isAddressCovered(host, ['127.0.0.1'])).toBe(false)
    expect(isAddressCovered(host, ['127.0.0.0/8', '10.0.0.0/8'])).toBe(false)
  })

  it.each(['::1', '[::1]'])('%s is covered when the allowlist names ::1', (host) => {
    expect(isAddressCovered(host, ['10.0.0.0/8', '::1'])).toBe(true)
    expect(isAddressCovered(host, ['::1/128'])).toBe(true)
  })

  it('stays undecided when other IPv6 entries could cover it', () => {
    expect(isAddressCovered('[::1]', ['127.0.0.1', 'fd00::/8'])).toBeNull()
  })

  it('keeps treating the hostname localhost as either family', () => {
    expect(isAddressCovered('localhost', ['127.0.0.1'])).toBe(true)
    expect(isAddressCovered('localhost', ['::1'])).toBe(true)
  })
})
