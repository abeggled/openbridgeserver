import { defineStore } from 'pinia'
import { ref } from 'vue'
import { knxprojApi } from '@/api/client'
import { DEFAULT_GROUP_ADDRESS_STYLE, GROUP_ADDRESS_STYLES } from '@/utils/groupAddress'

// The one source of the imported KNX project's group address style in the GUI
// (#1296). Fed from GET /knxproj/group-addresses, which also carries the
// migration's merge notes for admins. Show addresses with
// formatGa(address, knxProject.groupAddressStyle).
export const useKnxProjectStore = defineStore('knxProject', () => {
  const groupAddressStyle = ref(DEFAULT_GROUP_ADDRESS_STYLE)
  const mergeConflicts = ref([])
  let loaded = false
  let pending = null
  let latest = 0

  async function fetchProject(request) {
    try {
      const { data } = await knxprojApi.listGA({ size: 1 })
      if (request !== latest) return // a newer load() (after an import) answers instead
      const style = data?.group_address_style
      groupAddressStyle.value = GROUP_ADDRESS_STYLES.includes(style) ? style : DEFAULT_GROUP_ADDRESS_STYLE
      mergeConflicts.value = Array.isArray(data?.merge_conflicts) ? data.merge_conflicts : []
      loaded = true
    } catch {
      // Keep the last known style; the next load() tries again.
    }
  }

  // Loads once; `force` after an import or reset that may have changed the project.
  function load({ force = false } = {}) {
    if (!force && (loaded || pending)) return pending ?? Promise.resolve()
    latest += 1
    const request = fetchProject(latest).finally(() => {
      if (pending === request) pending = null
    })
    pending = request
    return request
  }

  return { groupAddressStyle, mergeConflicts, load }
})
