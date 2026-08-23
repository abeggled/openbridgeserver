/**
 * Binäre Byte-Formatierung (IEC, 1024er) für die RingBuffer-Admin-GUI (#919/#938).
 *
 * Einheitlich binär: B / KiB / MiB / GiB / TiB. Die Einheiten-Labels sind
 * technische IEC-Kürzel und werden bewusst nicht übersetzt (identisch in allen
 * Sprachen). Die reine Utility-Funktion enthält deshalb keine user-facing
 * Prosa und kein t() — nur normierte Einheiten-Symbole.
 */
const BINARY_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB']

/**
 * Formatiert eine Byte-Zahl binär (1024er-Schritte) im übergebenen Regionalformat.
 * Nicht-endliche oder nicht-positive Eingaben ergeben ``0 B``.
 *
 * @param {number|string} rawBytes
 * @param {string} [locale] BCP-47-Regionalformat (Issue #1073); Default `de-DE`
 * @returns {string} z. B. "3,0 MiB" oder "512 B"
 */
export function formatBytesBinary(rawBytes, locale = 'de-DE') {
  const bytes = Number(rawBytes)
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  let i = 0
  let v = bytes
  while (v >= 1024 && i < BINARY_UNITS.length - 1) {
    v /= 1024
    i += 1
  }
  const fractionDigits = v >= 100 || i === 0 ? 0 : 1
  let formatted
  try {
    formatted = new Intl.NumberFormat(locale, {
      minimumFractionDigits: fractionDigits,
      maximumFractionDigits: fractionDigits,
    }).format(v)
  } catch {
    formatted = String(v)
  }
  return `${formatted} ${BINARY_UNITS[i]}`
}
