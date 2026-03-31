/**
 * useDataPoint — Composable für einen einzelnen Live-DataPoint
 *
 * Gibt einen reaktiven Wert zurück, der via WebSocket aktualisiert wird.
 * Kümmert sich selbst um Subscribe/Unsubscribe.
 */

import { computed, onMounted, onUnmounted } from 'vue'
import { useDatapointsStore } from '@/stores/datapoints'
import type { DataPointValue } from '@/types'

export function useDataPoint(datapointId: string | null): {
  value: ReturnType<typeof computed<DataPointValue | null>>
} {
  const store = useDatapointsStore()

  onMounted(() => {
    if (datapointId) store.subscribe([datapointId])
  })

  onUnmounted(() => {
    if (datapointId) store.unsubscribe([datapointId])
  })

  const value = computed<DataPointValue | null>(() =>
    datapointId ? (store.values[datapointId] ?? null) : null
  )

  return { value }
}
