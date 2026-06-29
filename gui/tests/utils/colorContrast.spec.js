import { describe, it, expect } from 'vitest'
import {
  isValidColor,
  getAutoContrastText,
  CONTRAST_DARK_TEXT,
  CONTRAST_LIGHT_TEXT,
} from '@/utils/colorContrast'

describe('isValidColor', () => {
  it('returns false for null', () => {
    expect(isValidColor(null)).toBe(false)
  })

  it('returns false for undefined', () => {
    expect(isValidColor(undefined)).toBe(false)
  })

  it('returns false for empty string', () => {
    expect(isValidColor('')).toBe(false)
  })

  it('returns false for whitespace-only string', () => {
    expect(isValidColor('   ')).toBe(false)
  })

  it('returns true for a named color', () => {
    expect(isValidColor('red')).toBe(true)
  })

  it('returns true for a hex color', () => {
    expect(isValidColor('#ff0000')).toBe(true)
  })

  it('returns true for an rgb string', () => {
    expect(isValidColor('rgb(255,0,0)')).toBe(true)
  })

  it('returns false for a non-color string', () => {
    expect(isValidColor('not-a-color')).toBe(false)
  })
})

describe('getAutoContrastText', () => {
  it('returns dark text for a light color (white)', () => {
    expect(getAutoContrastText('white')).toBe(CONTRAST_DARK_TEXT)
  })

  it('returns light text for a dark color (black)', () => {
    expect(getAutoContrastText('black')).toBe(CONTRAST_LIGHT_TEXT)
  })

  it('falls back to dark text for null', () => {
    expect(getAutoContrastText(null)).toBe(CONTRAST_DARK_TEXT)
  })

  it('falls back to dark text for an invalid color string', () => {
    expect(getAutoContrastText('not-a-color')).toBe(CONTRAST_DARK_TEXT)
  })

  it('returns dark text for a light yellow', () => {
    expect(getAutoContrastText('#ffff00')).toBe(CONTRAST_DARK_TEXT)
  })

  it('returns light text for a dark navy color', () => {
    expect(getAutoContrastText('#0f172a')).toBe(CONTRAST_LIGHT_TEXT)
  })
})
