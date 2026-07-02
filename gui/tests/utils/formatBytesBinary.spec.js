/**
 * Tests for the shared binary byte formatter (#919/#938).
 *
 * All RingBuffer byte displays use IEC binary units (1024er): B / KiB / MiB /
 * GiB / TiB. No decimal MB/GB anywhere.
 */
import { describe, it, expect } from 'vitest'
import { formatBytesBinary } from '@/utils/formatBytesBinary'

describe('formatBytesBinary', () => {
  it('returns 0 B for non-positive / invalid input', () => {
    expect(formatBytesBinary(0)).toBe('0 B')
    expect(formatBytesBinary(-5)).toBe('0 B')
    expect(formatBytesBinary(null)).toBe('0 B')
    expect(formatBytesBinary(undefined)).toBe('0 B')
    expect(formatBytesBinary('nope')).toBe('0 B')
  })

  it('formats bytes below 1 KiB with the B unit', () => {
    expect(formatBytesBinary(512)).toBe('512 B')
  })

  it('uses KiB / MiB / GiB (binary, 1024er)', () => {
    expect(formatBytesBinary(1024)).toContain('KiB')
    expect(formatBytesBinary(1024 * 1024)).toContain('MiB')
    expect(formatBytesBinary(1024 * 1024 * 1024)).toContain('GiB')
    // 3 MiB → 3,0 MiB (one decimal, de-DE decimal comma)
    expect(formatBytesBinary(3 * 1024 * 1024)).toBe('3,0 MiB')
  })

  it('never emits decimal MB/GB units', () => {
    const out = formatBytesBinary(500 * 1024 * 1024)
    expect(out).toContain('MiB')
    expect(out).not.toMatch(/\bMB\b/)
    expect(out).not.toMatch(/\bGB\b/)
  })
})
