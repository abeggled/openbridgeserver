<template>
  <div class="flex flex-col gap-5">
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex-1 min-w-64">
        <h2 class="text-xl font-bold text-slate-800 dark:text-slate-100">{{ t('knxDevices.title') }}</h2>
        <p class="text-sm text-slate-500 mt-0.5">
          {{ t('knxDevices.subtitle', { count: pageData.total }) }}
        </p>
        <p class="text-xs text-slate-500 mt-1">
          {{ t('knxDevices.snapshotHint') }}
        </p>
      </div>
      <RouterLink
        v-if="canImport"
        class="btn-primary"
        data-testid="knx-devices-import-link"
        :to="{ name: 'Settings', query: { tab: 'importexport' }, hash: '#knx-project-import' }"
      >
        {{ t('knxDevices.importProject') }}
      </RouterLink>
    </div>

    <form class="grid grid-cols-1 lg:grid-cols-[minmax(14rem,1fr)_12rem_12rem_minmax(16rem,1fr)_auto] gap-2" @submit.prevent="applyFilters">
      <div class="relative">
        <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-4.35-4.35M17 11A6 6 0 115 11a6 6 0 0112 0z"/>
        </svg>
        <input
          v-model="filters.q"
          class="input pl-9 w-full"
          data-testid="knx-devices-search"
          :placeholder="t('knxDevices.searchPlaceholder')"
        />
      </div>
      <input
        v-model="filters.manufacturer"
        class="input"
        data-testid="knx-devices-manufacturer"
        :placeholder="t('knxDevices.manufacturerPlaceholder')"
      />
      <input
        v-model="filters.order_number"
        class="input"
        data-testid="knx-devices-order-number"
        :placeholder="t('knxDevices.orderNumberPlaceholder')"
      />
      <HierarchyCombobox
        v-model="filters.hierarchy_node_ids"
        data-testid="knx-devices-hierarchy-filter"
        :placeholder="t('knxDevices.hierarchyFilterPlaceholder')"
      />
      <button class="btn-primary justify-center" data-testid="knx-devices-apply">
        {{ t('common.search') }}
      </button>
    </form>

    <div
      v-if="error"
      class="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-600 dark:text-red-300"
      data-testid="knx-devices-error"
    >
      {{ error }}
    </div>

    <div class="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_24rem] gap-4 items-start">
      <section class="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
        <div v-if="loading" class="px-4 py-8 text-center text-sm text-slate-500" data-testid="knx-devices-loading">
          {{ t('common.loading') }}
        </div>
        <div v-else-if="!error && !devices.length" class="px-4 py-8 text-center text-sm text-slate-500" data-testid="knx-devices-empty">
          <p>{{ t('knxDevices.empty') }}</p>
          <RouterLink
            v-if="canImport"
            class="btn-secondary btn-sm mt-3 inline-flex"
            data-testid="knx-devices-empty-import-link"
            :to="{ name: 'Settings', query: { tab: 'importexport' }, hash: '#knx-project-import' }"
          >
            {{ t('knxDevices.importProject') }}
          </RouterLink>
        </div>
        <div v-else-if="devices.length" class="overflow-x-auto">
          <table class="min-w-full divide-y divide-slate-200 dark:divide-slate-700 text-sm">
            <thead class="bg-slate-50 dark:bg-slate-900/50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th class="px-4 py-3 text-left font-semibold">{{ t('knxDevices.table.pa') }}</th>
                <th class="px-4 py-3 text-left font-semibold">{{ t('knxDevices.table.name') }}</th>
                <th class="px-4 py-3 text-left font-semibold">{{ t('knxDevices.table.manufacturer') }}</th>
                <th class="px-4 py-3 text-left font-semibold">{{ t('knxDevices.table.orderNumber') }}</th>
                <th class="px-4 py-3 text-left font-semibold">{{ t('knxDevices.table.hierarchies') }}</th>
                <th class="px-4 py-3 text-left font-semibold">{{ t('knxDevices.table.appRef') }}</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100 dark:divide-slate-700">
              <tr
                v-for="device in devices"
                :key="device.pa"
                :class="[
                  'cursor-pointer transition-colors hover:bg-slate-50 dark:hover:bg-slate-700/50',
                  selectedDevice?.pa === device.pa ? 'bg-blue-500/10' : ''
                ]"
                :data-testid="`knx-device-row-${device.pa}`"
                @click="selectDevice(device)"
              >
                <td class="px-4 py-3 font-mono text-slate-900 dark:text-slate-100 whitespace-nowrap">{{ device.pa }}</td>
                <td class="px-4 py-3 text-slate-700 dark:text-slate-200">{{ valueOrDash(device.name) }}</td>
                <td class="px-4 py-3 text-slate-600 dark:text-slate-300">{{ valueOrDash(device.manufacturer) }}</td>
                <td class="px-4 py-3 font-mono text-slate-600 dark:text-slate-300">{{ valueOrDash(device.order_number) }}</td>
                <td class="px-4 py-3 text-slate-600 dark:text-slate-300">
                  <div v-if="device.hierarchy_links?.length" class="flex flex-wrap gap-1">
                    <span
                      v-for="link in device.hierarchy_links"
                      :key="`${link.tree_id}:${link.node_id}`"
                      class="inline-flex max-w-56 rounded bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-300"
                      :title="linkFullLabel(link)"
                    >
                      <PathLabel :segments="linkDisplayPath(link)" />
                    </span>
                  </div>
                  <span v-else class="text-slate-400">{{ t('knxDevices.noHierarchyLinks') }}</span>
                </td>
                <td class="px-4 py-3 font-mono text-xs text-slate-500">{{ valueOrDash(device.app_ref) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="px-4 py-8 text-center text-sm text-slate-500" data-testid="knx-devices-error-empty">
          {{ t('knxDevices.errorStateHint') }}
        </div>

        <div class="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 dark:border-slate-700 px-4 py-3 text-sm">
          <div class="flex flex-wrap items-center gap-3">
            <span class="text-slate-500" data-testid="knx-devices-page-label">
              {{ t('knxDevices.pageLabel', { page: pageData.page + 1, pages: pageData.pages }) }}
            </span>
            <button
              class="btn-ghost text-sm"
              data-testid="knx-devices-refresh"
              :disabled="loading"
              @click="loadDevices"
            >
              {{ t('knxDevices.reload') }}
            </button>
          </div>
          <div class="flex items-center gap-2">
            <button
              class="btn-secondary"
              data-testid="knx-devices-prev"
              :disabled="loading || pageData.page <= 0"
              @click="goToPage(pageData.page - 1)"
            >
              {{ t('knxDevices.previous') }}
            </button>
            <button
              class="btn-secondary"
              data-testid="knx-devices-next"
              :disabled="loading || pageData.page >= pageData.pages - 1"
              @click="goToPage(pageData.page + 1)"
            >
              {{ t('knxDevices.next') }}
            </button>
          </div>
        </div>
      </section>

      <aside class="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4 min-h-64">
        <div v-if="detailLoading" class="text-sm text-slate-500" data-testid="knx-device-detail-loading">
          {{ t('common.loading') }}
        </div>
        <div v-else-if="selectedDevice" data-testid="knx-device-detail">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 class="text-lg font-semibold text-slate-800 dark:text-slate-100">{{ selectedDevice.pa }}</h3>
              <p class="text-sm text-slate-500">{{ valueOrDash(selectedDevice.name) }}</p>
            </div>
            <button class="btn-ghost text-sm" data-testid="knx-device-clear-detail" @click="clearSelectedDevice">
              {{ t('common.close') }}
            </button>
          </div>

          <dl class="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-2 mt-4 text-sm">
            <dt class="text-slate-500">{{ t('knxDevices.table.manufacturer') }}</dt>
            <dd class="text-slate-800 dark:text-slate-100">{{ valueOrDash(selectedDevice.manufacturer) }}</dd>
            <dt class="text-slate-500">{{ t('knxDevices.table.orderNumber') }}</dt>
            <dd class="font-mono text-slate-800 dark:text-slate-100">{{ valueOrDash(selectedDevice.order_number) }}</dd>
            <dt class="text-slate-500">{{ t('knxDevices.table.appRef') }}</dt>
            <dd class="font-mono text-xs text-slate-800 dark:text-slate-100 break-all">{{ valueOrDash(selectedDevice.app_ref) }}</dd>
          </dl>

          <h4 class="mt-5 text-sm font-semibold text-slate-700 dark:text-slate-200">
            {{ t('knxDevices.hierarchyAssignments') }}
          </h4>
          <div v-if="canImport" class="mt-2 flex flex-col gap-2">
            <HierarchyCombobox
              v-model="deviceHierarchyIds"
              data-testid="knx-device-hierarchy-links"
              :placeholder="t('knxDevices.hierarchyAssignPlaceholder')"
            />
            <div class="flex items-center justify-end">
              <button
                class="btn-secondary btn-sm"
                data-testid="knx-device-save-hierarchy-links"
                :disabled="assignmentSaving"
                @click="saveDeviceHierarchyLinks"
              >
                {{ assignmentSaving ? t('common.saving') : t('common.save') }}
              </button>
            </div>
          </div>
          <div v-else class="mt-2 text-sm text-slate-500">
            <span v-if="!selectedDevice.hierarchy_links?.length">{{ t('knxDevices.noHierarchyLinks') }}</span>
            <span v-else class="flex flex-wrap gap-1">
              <span
                v-for="link in selectedDevice.hierarchy_links"
                :key="`${link.tree_id}:${link.node_id}`"
                class="inline-flex max-w-full rounded bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-300"
                :title="linkFullLabel(link)"
              >
                <PathLabel :segments="linkDisplayPath(link)" />
              </span>
            </span>
          </div>

          <h4 class="mt-5 text-sm font-semibold text-slate-700 dark:text-slate-200">
            {{ t('knxDevices.commObjectsTitle', { count: selectedDevice.comm_objects?.length ?? 0 }) }}
          </h4>
          <div v-if="!(selectedDevice.comm_objects?.length)" class="mt-2 text-sm text-slate-500">
            {{ t('knxDevices.noCommObjects') }}
          </div>
          <div v-else class="mt-2 flex flex-col gap-2">
            <div
              v-for="co in selectedDevice.comm_objects"
              :key="co.id"
              class="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2"
            >
              <div class="flex items-baseline justify-between gap-3">
                <span class="font-medium text-slate-800 dark:text-slate-100">{{ valueOrDash(co.name) }}</span>
                <span class="font-mono text-xs text-slate-500">{{ co.number }}</span>
              </div>
              <div class="mt-1 text-xs text-slate-500">{{ valueOrDash(co.datapoint_type) }}</div>
              <div class="mt-2 flex flex-wrap gap-1">
                <span
                  v-for="ga in co.ga_addresses"
                  :key="ga"
                  class="rounded bg-blue-500/10 px-2 py-0.5 font-mono text-xs text-blue-700 dark:text-blue-300"
                >
                  {{ ga }}
                </span>
                <span v-if="!co.ga_addresses?.length" class="text-xs text-slate-400">{{ t('knxDevices.noGaLinks') }}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="text-sm text-slate-500" data-testid="knx-device-detail-empty">
          {{ t('knxDevices.selectHint') }}
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { knxprojApi } from '@/api/client'
import HierarchyCombobox from '@/components/ui/HierarchyCombobox.vue'
import PathLabel from '@/components/ui/PathLabel.vue'
import { useAuthStore } from '@/stores/auth'
import { hierarchyDisplayPath } from '@/utils/hierarchyDisplay'

const { t } = useI18n()
const auth = useAuthStore()

const filters = reactive({
  q: '',
  manufacturer: '',
  order_number: '',
  hierarchy_node_ids: [],
})
const pageData = reactive({
  page: 0,
  size: 25,
  total: 0,
  pages: 1,
})

const devices = ref([])
const selectedDevice = ref(null)
const loading = ref(false)
const listRequestToken = ref(0)
const detailLoading = ref(false)
const detailRequestToken = ref(0)
const detailRequestPa = ref('')
const deviceHierarchyIds = ref([])
const assignmentSaving = ref(false)
const assignmentRequestToken = ref(0)
const error = ref('')

const canImport = computed(() => auth.isAdmin)
const hierarchyFilterNodeIds = computed(() => hierarchyNodeIdsFromSelection(filters.hierarchy_node_ids))
const requestParams = computed(() => ({
  q: filters.q.trim(),
  manufacturer: filters.manufacturer.trim(),
  order_number: filters.order_number.trim(),
  hierarchy_node_id: hierarchyFilterNodeIds.value.join(','),
  page: pageData.page,
  size: pageData.size,
}))

onMounted(() => {
  loadDevices()
})

async function loadDevices() {
  const requestToken = listRequestToken.value + 1
  listRequestToken.value = requestToken
  loading.value = true
  error.value = ''
  try {
    const { data } = await knxprojApi.listDevices(requestParams.value)
    if (listRequestToken.value !== requestToken) return
    const nextDevices = data.items ?? []
    devices.value = nextDevices
    pageData.total = data.total ?? 0
    pageData.page = data.page ?? pageData.page
    pageData.size = data.size ?? pageData.size
    pageData.pages = data.pages ?? 1
    clearDetailIfMissingFromList(nextDevices)
  } catch (err) {
    if (listRequestToken.value !== requestToken) return
    error.value = err.response?.data?.detail ?? t('knxDevices.loadError')
  } finally {
    if (listRequestToken.value === requestToken) {
      loading.value = false
    }
  }
}

function applyFilters() {
  pageData.page = 0
  loadDevices()
}

function goToPage(page) {
  const nextPage = Math.max(0, Math.min(page, pageData.pages - 1))
  if (nextPage === pageData.page) return
  pageData.page = nextPage
  loadDevices()
}

async function selectDevice(device) {
  assignmentRequestToken.value += 1
  assignmentSaving.value = false
  const requestToken = detailRequestToken.value + 1
  detailRequestToken.value = requestToken
  detailRequestPa.value = String(device.pa ?? '')
  detailLoading.value = true
  error.value = ''
  try {
    const { data } = await knxprojApi.getDevice(device.pa)
    if (detailRequestToken.value !== requestToken) return
    const responsePa = String(data?.pa ?? device.pa ?? '')
    if (!devices.value.some((item) => String(item.pa ?? '') === responsePa)) return
    selectedDevice.value = data
    deviceHierarchyIds.value = hierarchySelectionFromLinks(data.hierarchy_links)
    detailRequestPa.value = responsePa
  } catch (err) {
    if (detailRequestToken.value !== requestToken) return
    error.value = err.response?.data?.detail ?? t('knxDevices.detailError')
    selectedDevice.value = null
  } finally {
    if (detailRequestToken.value === requestToken) {
      detailLoading.value = false
    }
  }
}

function clearSelectedDevice() {
  detailRequestToken.value += 1
  assignmentRequestToken.value += 1
  detailRequestPa.value = ''
  selectedDevice.value = null
  deviceHierarchyIds.value = []
  detailLoading.value = false
  assignmentSaving.value = false
}

function clearDetailIfMissingFromList(nextDevices) {
  const devicePas = new Set(nextDevices.map((device) => String(device.pa ?? '')))
  const activePa = String(selectedDevice.value?.pa ?? detailRequestPa.value ?? '')
  if (activePa && !devicePas.has(activePa)) {
    clearSelectedDevice()
  }
}

function valueOrDash(value) {
  return value ? String(value) : '—'
}

function hierarchyNodeIdsFromSelection(selection) {
  return (Array.isArray(selection) ? selection : [])
    .map((id) => String(id).split(':').pop())
    .filter(Boolean)
}

function hierarchySelectionFromLinks(links) {
  return (Array.isArray(links) ? links : [])
    .map((link) => `${link.tree_id}:${link.node_id}`)
    .filter((id) => !id.endsWith(':'))
}

function linkNodePath(link) {
  if (!link) return []
  return [...(Array.isArray(link.node_path) ? link.node_path : []), link.node_name].filter(Boolean)
}

function linkDisplayPath(link) {
  const path = linkNodePath(link)
  const displayPath = hierarchyDisplayPath({
    treeName: link?.tree_name,
    path,
    displayDepth: link?.display_depth,
  })
  return displayPath.length ? displayPath : path
}

function linkFullLabel(link) {
  if (!link) return ''
  return [link.tree_name, ...linkNodePath(link)].filter(Boolean).join(' › ')
}

async function saveDeviceHierarchyLinks() {
  if (!selectedDevice.value) return
  const targetPa = String(selectedDevice.value.pa ?? '')
  const nodeIds = hierarchyNodeIdsFromSelection(deviceHierarchyIds.value)
  const requestToken = assignmentRequestToken.value + 1
  assignmentRequestToken.value = requestToken
  assignmentSaving.value = true
  error.value = ''
  try {
    const { data } = await knxprojApi.setDeviceHierarchyLinks(
      targetPa,
      { node_ids: nodeIds },
    )
    const idx = devices.value.findIndex((device) => String(device.pa) === String(data.pa))
    if (idx >= 0) {
      devices.value[idx] = { ...devices.value[idx], hierarchy_links: data.hierarchy_links || [] }
    }
    if (assignmentRequestToken.value !== requestToken) return
    if (String(selectedDevice.value?.pa ?? '') !== targetPa) return
    selectedDevice.value = data
    deviceHierarchyIds.value = hierarchySelectionFromLinks(data.hierarchy_links)
  } catch (err) {
    if (assignmentRequestToken.value !== requestToken) return
    error.value = err.response?.data?.detail ?? t('knxDevices.assignmentError')
  } finally {
    if (assignmentRequestToken.value === requestToken) {
      assignmentSaving.value = false
    }
  }
}
</script>
