import chroma from 'chroma-js'

export const CONTRAST_DARK_TEXT = '#0f172a'
export const CONTRAST_LIGHT_TEXT = '#ffffff'

export function isValidColor(value) {
  if (value === null || value === undefined) return false
  const trimmed = String(value).trim()
  if (!trimmed) return false
  try {
    return chroma.valid(trimmed)
  } catch {
    return false
  }
}

export function getAutoContrastText(color) {
  if (!isValidColor(color)) return CONTRAST_DARK_TEXT
  try {
    const cWhite = chroma.contrast(color, CONTRAST_LIGHT_TEXT)
    const cDark = chroma.contrast(color, CONTRAST_DARK_TEXT)
    return cWhite > cDark ? CONTRAST_LIGHT_TEXT : CONTRAST_DARK_TEXT
  } catch {
    return CONTRAST_DARK_TEXT
  }
}
