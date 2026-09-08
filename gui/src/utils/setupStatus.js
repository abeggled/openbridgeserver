/**
 * First-run setup state (#1229).
 *
 * An installation without an owner answers nothing but its setup page, so the
 * router has to know before it resolves the first route. The answer is cached:
 * it can only change once per process — when the owner is created — and the
 * router guard runs on every navigation.
 */
import { setupApi } from '@/api/client'

let cached = null

export async function fetchSetupRequired() {
  if (cached !== null) return cached
  try {
    const { data } = await setupApi.status()
    cached = !!data?.setup_required
  } catch {
    // No answer (offline, old backend without the route): behave like a
    // configured installation and let the normal auth flow report the problem.
    cached = false
  }
  return cached
}

export function markSetupComplete() {
  cached = false
}

export function resetSetupStatusCache() {
  cached = null
}
