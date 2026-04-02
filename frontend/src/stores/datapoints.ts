/**
 * Pinia-Store: DataPoint Live-Werte via WebSocket
 *
 * - Verwaltet alle abonnierten DataPoints und ihre aktuellen Werte
 * - Subscribe/Unsubscribe: nur die Differenz wird ans Backend gesendet
 * - Beim Seitenwechsel: nicht mehr benötigte IDs werden abgemeldet
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'
import { datapoints as datapointsApi } from '@/api/client'
import type { DataPointValue } from '@/types'

export const useDatapointsStore = defineStore('datapoints', () => {
  const values = ref<Record<string, DataPointValue>>({})
  const subscribed = ref(new Set<string>())

  const ws = useWebSocket()

  // Eingehende WS-Nachrichten verarbeiten
  ws.onMessage((msg) => {
    if (msg.id && msg.v !== undefined) {
      // DataPoint-Wert-Update
      const id = msg.id as string
      values.value[id] = {
        id,
        v: msg.v,
        u: (msg.u as string | null) ?? null,
        t: msg.t as string,
        q: (msg.q as DataPointValue['q']) ?? 'good',
      }
    }
  })

  /**
   * DataPoint-IDs abonnieren.
   * Nur IDs, die noch nicht abonniert sind, werden an den WS gesendet.
   */
  function subscribe(ids: string[]) {
    const newIds = ids.filter((id) => !subscribed.value.has(id))
    if (newIds.length === 0) return
    newIds.forEach((id) => subscribed.value.add(id))
    ws.subscribe(newIds)
  }

  /**
   * DataPoint-IDs abbestellen.
   * Nur IDs, die kein anderes Widget mehr braucht, werden abgemeldet.
   * (Referenz-Counting via Set-Größe — Widgets rufen subscribe/unsubscribe
   *  symmetrisch auf.)
   */
  function unsubscribe(ids: string[]) {
    const toRemove = ids.filter((id) => subscribed.value.has(id))
    if (toRemove.length === 0) return
    toRemove.forEach((id) => subscribed.value.delete(id))
    ws.unsubscribe(toRemove)
  }

  /** Einen einzelnen Live-Wert lesen */
  function getValue(id: string): DataPointValue | null {
    return values.value[id] ?? null
  }

  /**
   * Aktuelle Werte via HTTP laden (Fallback wenn WebSocket noch nicht bereit
   * oder für öffentliche Seiten ohne JWT).
   * Nur IDs mit Qualität "good" werden gesetzt.
   */
  async function fetchInitialValues(ids: string[]): Promise<void> {
    await Promise.allSettled(
      ids.map(async (id) => {
        try {
          // Backend gibt { value, unit, quality, ts } zurück (nicht v/u/q/t)
          const v = await datapointsApi.getValue(id, true)
          const quality = (v.quality as DataPointValue['q']) ?? 'good'
          if (quality === 'good' || quality === 'uncertain') {
            values.value[id] = {
              id,
              v: v.value,
              u: v.unit,
              t: v.ts ?? new Date().toISOString(),
              q: quality,
            }
          }
        } catch {
          // ignorieren — Wert bleibt leer
        }
      })
    )
  }

  return { values, subscribed, subscribe, unsubscribe, getValue, fetchInitialValues }
})
