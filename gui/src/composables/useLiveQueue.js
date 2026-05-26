/**
 * useLiveQueue — buffer incoming live WebSocket entries and flush them
 * onto a backing list in batches.
 *
 * Why a composable?
 *   The pause/resume + batch-flush pattern is RingBuffer-specific but
 *   independent enough from RingBufferView's layout to live on its own
 *   and unit-test in isolation. Extracted in #438 to keep the view file
 *   small.
 *
 * Usage:
 *   const { paused, queuedCount, enqueue, pause, resume, dispose }
 *       = useLiveQueue(entriesRef, { onFlush })
 */
import { ref, computed } from 'vue'

const LIVE_BATCH_SIZE = 200
const LIVE_FLUSH_INTERVAL_MS = 60
const LIVE_QUEUE_MAX = 5000

export function useLiveQueue(entriesRef, { maxEntries = 500, onFlush } = {}) {
  const paused = ref(false)
  const queue = ref([])
  const queuedCount = computed(() => queue.value.length)

  let flushTimer = null

  function scheduleFlush() {
    if (paused.value || flushTimer) return
    flushTimer = setTimeout(() => {
      flushTimer = null
      void flushQueue()
    }, LIVE_FLUSH_INTERVAL_MS)
  }

  async function flushQueue() {
    if (paused.value || !queue.value.length) return
    const batch = queue.value.splice(0, LIVE_BATCH_SIZE)
    if (batch.length) {
      entriesRef.value = [...batch.reverse(), ...entriesRef.value].slice(0, maxEntries)
      await onFlush?.()
    }
    if (queue.value.length) scheduleFlush()
  }

  function enqueue(entry) {
    queue.value.push(entry)
    if (queue.value.length > LIVE_QUEUE_MAX) {
      queue.value.splice(0, queue.value.length - LIVE_QUEUE_MAX)
    }
    if (!paused.value) scheduleFlush()
  }

  function pause() {
    paused.value = true
  }

  function resume() {
    paused.value = false
    scheduleFlush()
  }

  function clear() {
    queue.value = []
  }

  function dispose() {
    clearTimeout(flushTimer)
    flushTimer = null
  }

  return { paused, queuedCount, enqueue, pause, resume, clear, dispose }
}
