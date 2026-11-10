/**
 * First-run setup state (#1229).
 *
 * An installation without an owner answers nothing but its setup page, so the
 * router has to know before it resolves the first route. The answer is cached:
 * it can only change once per process — when the owner is created — and the
 * router guard runs on every navigation.
 */
import { setupApi } from '@/api/client'

// Backstop for the router guard: every navigation awaits this answer, so a
// request that is accepted but never completed would leave the app on a blank
// page for good. The HTTP call carries its own, shorter timeout — this bound
// only catches a status call that fails to settle at all.
const ANSWER_DEADLINE_MS = 10000

let cached = null

export async function fetchSetupRequired() {
  if (cached !== null) return cached
  let timer
  try {
    const answer = await Promise.race([
      setupApi.status(),
      new Promise((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error('setup_status_timeout')), ANSWER_DEADLINE_MS)
      }),
    ])
    cached = !!answer?.data?.setup_required
  } catch {
    // No answer (offline, stalled, old backend without the route): behave like
    // a configured installation and let the normal auth flow report the problem.
    cached = false
  } finally {
    clearTimeout(timer)
  }
  return cached
}

export function markSetupComplete() {
  cached = false
}

export function resetSetupStatusCache() {
  cached = null
}
