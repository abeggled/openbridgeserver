/**
 * Rücksprung zur Startseite des Geräteprofils nach einer Zeit ohne Bedienung.
 *
 * Jede Berührung, Maus-, Rad- oder Tastatureingabe startet die Wartezeit neu.
 * Nur in der normalen Ansicht (Seite oder Übersicht) wird zurückgesprungen —
 * nie aus dem Editor, der Verwaltung oder einer Login-/PIN-Eingabe.
 */

import { onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useDeviceStore } from '@/stores/device'

const ACTIVITY_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'] as const
const RETURN_FROM = new Set(['viewer', 'tree'])

export function useIdleReturn() {
  const router = useRouter()
  const device = useDeviceStore()
  let timer: ReturnType<typeof setTimeout> | null = null

  function returnToStart() {
    const current = router.currentRoute.value
    if (!RETURN_FROM.has(String(current.name))) return
    const target = router.resolve(device.startRoute)
    if (target.fullPath === current.fullPath) return
    void router.push(target)
  }

  function arm() {
    if (timer !== null) clearTimeout(timer)
    timer = device.returnAfterMs > 0 ? setTimeout(returnToStart, device.returnAfterMs) : null
  }

  onMounted(() => {
    for (const event of ACTIVITY_EVENTS) window.addEventListener(event, arm, { passive: true })
    arm()
  })

  onUnmounted(() => {
    for (const event of ACTIVITY_EVENTS) window.removeEventListener(event, arm)
    if (timer !== null) clearTimeout(timer)
  })

  // Profilwechsel oder geänderte Zeit gelten sofort; ein Seitenwechsel zählt als Bedienung.
  watch(() => device.returnAfterMs, arm)
  watch(() => router.currentRoute.value.fullPath, arm)
}
