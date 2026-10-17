/**
 * Helpers for the webhook ingress allowlist (issue #1256 follow-up).
 *
 * The backend is the authority on what a valid entry is — `ipaddress` parses
 * both families there. These helpers only exist so the Admin GUI can give
 * immediate feedback while typing, and so it can warn when the call URL it
 * hands the operator is one their own allowlist would reject. They therefore
 * decide IPv4 fully and stay deliberately silent about anything else, rather
 * than guessing with a hand-rolled IPv6 parser.
 */

const IPV4_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/

/** Hostnames a browser uses for the machine OBS itself runs on. */
const LOOPBACK_HOSTNAMES = new Set(['localhost', '127.0.0.1', '[::1]', '::1'])

/**
 * Coerce a stored allowlist into a list of strings. A binding saved before the
 * field became a list holds a comma-separated string, and the backend schema
 * accepts that too, so every consumer goes through this instead of assuming an
 * array.
 */
export function normalizeEntries(value) {
  if (Array.isArray(value)) return value.map(entry => String(entry))
  return String(value ?? '')
    .split(/[,;\s]+/)
    .filter(Boolean)
}

/** Parse an IPv4 literal into an unsigned 32-bit number, or null. */
export function parseIpv4(text) {
  const match = IPV4_RE.exec(String(text ?? '').trim())
  if (!match) return null
  let value = 0
  for (let i = 1; i <= 4; i += 1) {
    // `ipaddress` on the backend refuses leading zeros ("010"), so accepting
    // them here would show no error for an entry that fails with a 422 on save.
    if (match[i].length > 1 && match[i].startsWith('0')) return null
    const octet = Number(match[i])
    if (octet > 255) return null
    value = value * 256 + octet
  }
  return value >>> 0
}

/**
 * Split an entry into { address, prefixLength }, or null when it is not
 * IPv4-shaped. A bare address is treated as a /32 host route.
 */
export function parseIpv4Entry(text) {
  const raw = String(text ?? '').trim()
  if (!raw) return null
  const slash = raw.indexOf('/')
  if (slash === -1) {
    const address = parseIpv4(raw)
    return address === null ? null : { address, prefixLength: 32 }
  }
  const address = parseIpv4(raw.slice(0, slash))
  const suffix = raw.slice(slash + 1).trim()
  if (address === null || !/^\d{1,2}$/.test(suffix)) return null
  const prefixLength = Number(suffix)
  if (prefixLength < 0 || prefixLength > 32) return null
  return { address, prefixLength }
}

/**
 * Classify an entry for inline feedback: 'valid' for IPv4 we fully understand,
 * 'unchecked' for anything the backend will have to decide (IPv6 and the like),
 * 'invalid' only when it looks like IPv4 but cannot be one.
 */
export function classifyEntry(text) {
  const raw = String(text ?? '').trim()
  if (!raw) return 'invalid'
  if (parseIpv4Entry(raw) !== null) return 'valid'
  // Anything with a colon is IPv6-shaped; anything else that is not
  // IPv4-shaped is a typo we can call out with confidence.
  if (raw.includes(':')) return 'unchecked'
  if (/^[\d./]+$/.test(raw)) return 'invalid'
  return 'invalid'
}

function ipv4Covered(address, entry) {
  const parsed = parseIpv4Entry(entry)
  if (parsed === null) return null
  if (parsed.prefixLength === 0) return true
  const mask = (0xffffffff << (32 - parsed.prefixLength)) >>> 0
  return ((address & mask) >>> 0) === ((parsed.address & mask) >>> 0)
}

/**
 * Whether `host` is covered by `entries`.
 *
 * Returns true/false when it can be decided, and null when it cannot — a
 * hostname that would need DNS, or an IPv6 address we do not parse. Callers
 * must treat null as "no verdict" and stay quiet rather than warn, because a
 * wrong warning about a working URL is worse than no warning at all.
 *
 * An empty allowlist covers everything, which is what the backend does too.
 */
export function isAddressCovered(host, entries) {
  const list = normalizeEntries(entries).map(entry => entry.trim()).filter(Boolean)
  if (list.length === 0) return true

  const hostText = String(host ?? '').trim().toLowerCase()
  // A literal IPv6 loopback already names the address family: the caller IS
  // ::1, so an IPv4 entry such as 127.0.0.1 cannot cover it. IPv6 entries other
  // than ::1 are not parsed here, so they leave the verdict open.
  if (hostText === '::1' || hostText === '[::1]') {
    if (list.some(entry => entry === '::1' || entry === '::1/128')) return true
    return list.every(entry => parseIpv4Entry(entry) !== null) ? false : null
  }

  const loopback = isLoopbackHost(host)
  // `localhost` is reached as either 127.0.0.1 or ::1 and we cannot tell which,
  // so an allowlist naming either one counts as covering it.
  if (loopback && list.some(entry => entry === '::1' || entry === '::1/128')) return true

  const address = parseIpv4(loopback ? '127.0.0.1' : String(host ?? '').trim())
  if (address === null) return null

  let verdict = false
  for (const entry of list) {
    const covered = ipv4Covered(address, entry)
    if (covered === true) return true
    if (covered === null) verdict = null // an entry we cannot judge forbids a "no"
  }
  return verdict
}

export function isLoopbackHost(host) {
  const value = String(host ?? '').trim().toLowerCase()
  return LOOPBACK_HOSTNAMES.has(value) || value.startsWith('127.')
}
