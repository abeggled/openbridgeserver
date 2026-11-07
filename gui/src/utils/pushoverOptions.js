// Pushover message options shared by the MESSAGE binding form and the
// notification block (issue #1328). Mirrors PushoverMessageOptions in
// obs/adapters/message/providers/pushover.py — the backend validates again.

export const PUSHOVER_PROVIDER = 'pushover'
export const PUSHOVER_PRIORITIES = [-2, -1, 0, 1, 2]
export const PUSHOVER_EMERGENCY_PRIORITY = 2
export const PUSHOVER_MIN_RETRY_SECONDS = 30
export const PUSHOVER_MAX_EXPIRE_SECONDS = 10800
export const PUSHOVER_FORMATS = ['text', 'html', 'monospace']
export const PUSHOVER_TEXT_FIELDS = ['image_url', 'url', 'url_title', 'sound']
export const PUSHOVER_NUMBER_FIELDS = ['ttl', 'timestamp', 'retry', 'expire']
export const PUSHOVER_MAX_LENGTHS = { image_url: 2048, url: 512, url_title: 100, sound: 32 }

// Sounds documented by Pushover; free input stays possible for custom sounds.
export const PUSHOVER_SOUNDS = [
  'pushover', 'bike', 'bugle', 'cashregister', 'classical', 'cosmic', 'falling', 'gamelan',
  'incoming', 'intermission', 'magic', 'mechanical', 'pianobar', 'siren', 'spacealarm', 'tugboat',
  'alien', 'climb', 'persistent', 'echo', 'updown', 'vibrate', 'none',
]

const SOUND_RE = /^[A-Za-z0-9_-]+$/

export function sendsNotification(strategy) {
  return ['send_only', 'send_and_archive'].includes(strategy ?? 'send_only')
}

export function normalizeFormat(value) {
  const format = typeof value === 'string' ? value.trim().toLowerCase() : ''
  return PUSHOVER_FORMATS.includes(format) ? format : 'text'
}

export function hasPushoverTarget(refs) {
  return Array.isArray(refs) && refs.some((ref) => ref?.provider === PUSHOVER_PROVIDER)
}

function isBlank(value) {
  return value == null || (typeof value === 'string' && value.trim() === '')
}

export function normalizePriority(value) {
  if (isBlank(value)) return null
  const number = Number(value)
  return Number.isInteger(number) ? number : null
}

/**
 * Only the options that are actually set — unset options never reach Pushover.
 * retry/expire are only shown for emergency priority, so below it a leftover
 * value is dropped instead of being saved or validated.
 */
export function normalizePushoverOptions(options, priority = null) {
  const result = {}
  if (!options || typeof options !== 'object') return result
  for (const field of PUSHOVER_TEXT_FIELDS) {
    if (!isBlank(options[field])) result[field] = String(options[field]).trim()
  }
  for (const field of PUSHOVER_NUMBER_FIELDS) {
    if (!isBlank(options[field])) result[field] = Number(options[field])
  }
  const format = normalizeFormat(options.format)
  if (format !== 'text') result.format = format
  if (normalizePriority(priority) !== PUSHOVER_EMERGENCY_PRIORITY) {
    delete result.retry
    delete result.expire
  }
  return result
}

/**
 * Validation errors keyed by field, each value an i18n key below
 * `adapters.pushoverOptions.errors`. Pure function: returns keys, not text.
 */
export function pushoverOptionErrors(options, priority, { urlFromInput = false } = {}) {
  const opts = normalizePushoverOptions(options, priority)
  const errors = {}
  for (const field of PUSHOVER_NUMBER_FIELDS) {
    if (field in opts && !Number.isInteger(opts[field])) errors[field] = 'adapters.pushoverOptions.errors.integer'
  }
  if (!errors.retry && 'retry' in opts && opts.retry < PUSHOVER_MIN_RETRY_SECONDS) {
    errors.retry = 'adapters.pushoverOptions.errors.retryMin'
  }
  if (!errors.expire && 'expire' in opts && (opts.expire <= 0 || opts.expire > PUSHOVER_MAX_EXPIRE_SECONDS)) {
    errors.expire = 'adapters.pushoverOptions.errors.expireRange'
  }
  if (!errors.ttl && 'ttl' in opts && opts.ttl <= 0) errors.ttl = 'adapters.pushoverOptions.errors.positive'
  if (!errors.timestamp && 'timestamp' in opts && opts.timestamp <= 0) errors.timestamp = 'adapters.pushoverOptions.errors.positive'
  if (normalizePriority(priority) === PUSHOVER_EMERGENCY_PRIORITY) {
    if (!('retry' in opts)) errors.retry = 'adapters.pushoverOptions.errors.retryRequired'
    if (!('expire' in opts)) errors.expire = 'adapters.pushoverOptions.errors.expireRequired'
  }
  for (const [field, max] of Object.entries(PUSHOVER_MAX_LENGTHS)) {
    if (field in opts && opts[field].length > max) errors[field] = 'adapters.pushoverOptions.errors.tooLong'
  }
  if (!errors.url_title && !urlFromInput && 'url_title' in opts && !('url' in opts)) errors.url_title = 'adapters.pushoverOptions.errors.urlTitleWithoutUrl'
  if (!errors.sound && 'sound' in opts && !SOUND_RE.test(opts.sound)) errors.sound = 'adapters.pushoverOptions.errors.soundInvalid'
  return errors
}
