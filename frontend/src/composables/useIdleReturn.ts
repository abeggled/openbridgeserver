/**
 * Rücksprung zur Startseite des Geräteprofils nach einer Zeit ohne Bedienung.
 *
 * Jede Berührung, Maus-, Rad- oder Tastatureingabe startet die Wartezeit neu.
 * Nur in der normalen Ansicht (Seite oder Übersicht) wird zurückgesprungen —
 * nie aus dem Editor, der Verwaltung oder einer Login-/PIN-Eingabe.
 *
 * Bedienung innerhalb eines eingebetteten Fensters (IFrame-Widget) erreicht die
 * Visu nicht. Das Tippen hinein (der Fokus wandert in den Rahmen) zählt als
 * Bedienung und startet die Wartezeit neu — mehr ist nicht zu sehen, und ein
 * unbeaufsichtigtes Panel muss auch aus einem Rahmen heraus zurückspringen.
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
    // Nur der Pfad zählt: ein Kiosk, der über `/visu/<start>?device=…` kam, ist schon dort.
    if (target.path === current.path) return
    void router.push(target)
  }

  function arm() {
    if (timer !== null) clearTimeout(timer)
    timer = device.returnAfterMs > 0 ? setTimeout(returnToStart, device.returnAfterMs) : null
  }

  function pause() {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  // Das Fenster verliert den Fokus an einen Rahmen: der Nutzer hat hineingetippt.
  function onBlur() {
    if (document.activeElement?.tagName === 'IFRAME') arm()
  }

  onMounted(() => {
    for (const event of ACTIVITY_EVENTS) window.addEventListener(event, arm, { passive: true })
    window.addEventListener('blur', onBlur)
    window.addEventListener('focus', arm)
    arm()
  })

  onUnmounted(() => {
    for (const event of ACTIVITY_EVENTS) window.removeEventListener(event, arm)
    window.removeEventListener('blur', onBlur)
    window.removeEventListener('focus', arm)
    pause()
  })

  // Profilwechsel oder geänderte Zeit gelten sofort; ein Seitenwechsel zählt als Bedienung.
  watch(() => device.returnAfterMs, arm)
  watch(() => router.currentRoute.value.fullPath, arm)
}
