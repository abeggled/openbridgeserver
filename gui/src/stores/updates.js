import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { updatesApi } from '@/api/client'

// Update check status (issue #1370). The backend checks GitHub on its own
// schedule and caches the result; this store only reads that cache, so any
// number of open browsers cause no requests to GitHub.
export const useUpdatesStore = defineStore('updates', () => {
  const status       = ref(null)
  const releaseNotes = ref([])
  const notesLoading = ref(false)
  const notesError   = ref(false)
  const checking     = ref(false)
  const saving       = ref(false)
  const statusError  = ref(false)
  // Bumped by the notice's "Details" link: SettingsView switches to the
  // Updates tab even when it is already open on another tab and the URL
  // already reads ?tab=updates (#1370).
  const updatesTabRequests = ref(0)

  // Status answers can arrive in any order: the notice and the Settings panel
  // read the status, a manual check and a channel change may overlap, and the
  // backend serializes the mutations itself. Every request takes a ticket and
  // an answer is shown only if no newer one has been shown yet. A read that
  // overlapped a mutation may carry the state from before it and is dropped.
  // Once overlapping mutations are all done, the store reads the final state
  // back: their answers say nothing about which of them the backend ran last,
  // and one of them may have failed after another one succeeded.
  let tickets = 0
  let shownTicket = 0
  let mutationEpoch = 0  // bumped when a mutation starts and when it ends
  let pendingMutations = 0
  let overlapped = false
  // Release notes come from two modals (notice, Settings) around channel
  // switches: only the newest request may set them.
  let notesRequests = 0

  const updateAvailable = computed(() => status.value?.update_available === true)

  function show(ticket, data) {
    if (ticket < shownTicket) return
    shownTicket = ticket
    status.value = data
    statusError.value = false
  }

  async function fetchStatus() {
    const ticket = ++tickets
    const epoch = mutationEpoch
    const current = () => epoch === mutationEpoch && pendingMutations === 0
    try {
      const { data } = await updatesApi.status()
      if (current()) show(ticket, data)
    } catch {
      // Non-critical for the notice, which simply stays hidden; the Settings
      // panel shows the failure instead of loading forever.
      if (current() && ticket > shownTicket) statusError.value = true
    }
    return status.value
  }

  async function fetchReleaseNotes() {
    const request = ++notesRequests
    notesLoading.value = true
    notesError.value = false
    try {
      const { data } = await updatesApi.releaseNotes()
      if (request === notesRequests) releaseNotes.value = data
    } catch {
      if (request === notesRequests) {
        notesError.value = true
        releaseNotes.value = []
      }
    } finally {
      if (request === notesRequests) notesLoading.value = false
    }
    return releaseNotes.value
  }

  async function mutate(flag, request) {
    flag.value = true
    mutationEpoch += 1
    if (pendingMutations > 0) overlapped = true
    pendingMutations += 1
    const ticket = ++tickets
    try {
      const { data } = await request()
      show(ticket, data)
      // Notes loaded before or during the check or channel switch describe the
      // previous result: read them again. Its newer request supersedes any
      // still in flight. Never loaded means nothing is shown, nothing to renew.
      if (notesRequests > 0) fetchReleaseNotes()
      return data
    } finally {
      mutationEpoch += 1
      pendingMutations -= 1
      flag.value = false
      if (pendingMutations === 0 && overlapped) {
        overlapped = false
        fetchStatus()
      }
    }
  }

  function checkNow() {
    return mutate(checking, () => updatesApi.check())
  }

  function requestUpdatesTab() {
    updatesTabRequests.value += 1
  }

  function setChannel(channel) {
    return mutate(saving, () => updatesApi.setChannel(channel))
  }

  return {
    status,
    releaseNotes,
    notesLoading,
    notesError,
    checking,
    saving,
    statusError,
    updateAvailable,
    updatesTabRequests,
    requestUpdatesTab,
    fetchStatus,
    fetchReleaseNotes,
    checkNow,
    setChannel,
  }
})
