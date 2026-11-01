/**
 * Live-Liste der aktiven Visu-Alarme über den eigenen Alarm-Kanal.
 *
 * Der Kanal ist bewusst getrennt von der Datenpunkt-WebSocket-Verbindung: er
 * steht jeder Visu offen — auch ohne Anmeldung und auf der Übersicht — und
 * überträgt nur die Alarm-Anzeigen, nie Werte. Der Server schickt nach jeder
 * Änderung die vollständige Liste; abgerissene Verbindungen werden mit
 * wachsender Wartezeit neu aufgebaut.
 */

import { onMounted, onUnmounted, ref } from 'vue'
import type { ActiveVisuAlarm } from '@/api/client'

const MIN_DELAY = 1000
const MAX_DELAY = 30_000

function alarmsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/v1/system/visu-alarms/ws`
}

export function useVisuAlarms() {
  const alarms = ref<ActiveVisuAlarm[]>([])
  let socket: WebSocket | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let delay = MIN_DELAY
  let stopped = false

  function connect() {
    socket = new WebSocket(alarmsUrl())
    socket.onopen = () => { delay = MIN_DELAY }
    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(String(event.data))
        if (data?.action === 'visu_alarms' && Array.isArray(data.alarms)) alarms.value = data.alarms
      } catch {
        // Kein JSON — ignorieren.
      }
    }
    socket.onclose = () => {
      socket = null
      if (stopped) return
      timer = setTimeout(connect, delay)
      delay = Math.min(delay * 2, MAX_DELAY)
    }
  }

  onMounted(connect)
  onUnmounted(() => {
    stopped = true
    if (timer !== null) clearTimeout(timer)
    socket?.close()
  })

  return { alarms }
}
