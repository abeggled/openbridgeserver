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

  // Bumped when a check or channel change starts and again when it ends. A
  // status read that overlapped one may carry the state from before it and
  // must not overwrite the mutation's answer.
  let mutations = 0

  const updateAvailable = computed(() => status.value?.update_available === true)

  async function fetchStatus() {
    const seen = mutations
    try {
      const { data } = await updatesApi.status()
      if (seen === mutations) {
        status.value = data
        statusError.value = false
      }
    } catch {
      // Non-critical for the notice, which simply stays hidden; the Settings
      // panel shows the failure instead of loading forever.
      if (seen === mutations) statusError.value = true
    }
    return status.value
  }

  async function fetchReleaseNotes() {
    notesLoading.value = true
    notesError.value = false
    try {
      const { data } = await updatesApi.releaseNotes()
      releaseNotes.value = data
    } catch {
      notesError.value = true
      releaseNotes.value = []
    } finally {
      notesLoading.value = false
    }
    return releaseNotes.value
  }

  async function checkNow() {
    checking.value = true
    mutations += 1
    try {
      const { data } = await updatesApi.check()
      status.value = data
      return data
    } finally {
      mutations += 1
      checking.value = false
    }
  }

  function requestUpdatesTab() {
    updatesTabRequests.value += 1
  }

  async function setChannel(channel) {
    saving.value = true
    mutations += 1
    try {
      const { data } = await updatesApi.setChannel(channel)
      status.value = data
      return data
    } finally {
      mutations += 1
      saving.value = false
    }
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
