<template>
  <Modal
    :model-value="modelValue"
    :title="$t('settings.users.rights.title', { username })"
    max-width="2xl"
    @update:modelValue="close"
  >
    <div class="flex flex-col gap-5" data-testid="user-rights-editor">
      <ol class="grid grid-cols-2 gap-2 sm:grid-cols-4" :aria-label="$t('settings.users.rights.progress')">
        <li
          v-for="item in steps"
          :key="item.number"
          :class="[
            'rounded-lg border px-3 py-2 text-xs',
            step === item.number
              ? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300'
              : 'border-slate-200 text-slate-500 dark:border-slate-700 dark:text-slate-400',
          ]"
          :data-testid="`rights-step-${item.number}`"
        >
          <span class="block font-semibold">{{ item.number }}. {{ item.label }}</span>
        </li>
      </ol>

      <div v-if="loading" class="flex justify-center py-10" data-testid="rights-loading">
        <Spinner />
      </div>

      <div v-else-if="loadError" class="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-500" data-testid="rights-load-error">
        {{ loadError }}
      </div>

      <template v-else>
        <section v-if="step === 1" class="flex flex-col gap-3" data-testid="rights-role-step">
          <div>
            <h4 class="font-semibold text-slate-800 dark:text-slate-100">{{ $t('settings.users.rights.roleTitle') }}</h4>
            <p class="mt-1 text-sm text-slate-500">{{ $t('settings.users.rights.roleHint') }}</p>
          </div>
          <div
            v-if="mixedRoles"
            class="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300"
            data-testid="mixed-role-warning"
          >
            {{ $t('settings.users.rights.mixedRoleWarning') }}
          </div>
          <div class="grid gap-2 sm:grid-cols-2">
            <label
              v-for="option in roleOptions"
              :key="option.value"
              :class="[
                'cursor-pointer rounded-lg border p-3 transition-colors',
                selectedRole === option.value
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-500/10'
                  : 'border-slate-200 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800/40',
              ]"
            >
              <span class="flex items-center gap-2">
                <input v-model="selectedRole" type="radio" name="user-role" :value="option.value" />
                <span class="font-medium text-slate-800 dark:text-slate-100">{{ option.label }}</span>
              </span>
              <span class="mt-1 block pl-6 text-xs text-slate-500">{{ option.description }}</span>
            </label>
          </div>
          <label v-if="originalNodeIds.length" class="flex items-start gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
            <input
              v-model="reassignExisting"
              type="checkbox"
              :disabled="!selectedRole"
              class="mt-0.5"
              data-testid="bulk-role-reassign"
            />
            <span>
              <span class="block text-sm font-medium text-slate-800 dark:text-slate-100">{{ $t('settings.users.rights.bulkRoleTitle') }}</span>
              <span class="mt-1 block text-xs text-slate-500">{{ $t('settings.users.rights.bulkRoleHint') }}</span>
            </span>
          </label>
        </section>

        <section v-else-if="step === 2" class="flex flex-col gap-3" data-testid="rights-scope-step">
          <div>
            <h4 class="font-semibold text-slate-800 dark:text-slate-100">{{ $t('settings.users.rights.scopeTitle') }}</h4>
            <p class="mt-1 text-sm text-slate-500">{{ $t('settings.users.rights.scopeHint') }}</p>
          </div>
          <div v-if="!hierarchyNodes.length" class="rounded-lg border border-slate-200 p-4 text-sm text-slate-500 dark:border-slate-700">
            {{ $t('settings.users.rights.noScopes') }}
          </div>
          <div v-else class="max-h-80 divide-y divide-slate-200 overflow-y-auto rounded-lg border border-slate-200 dark:divide-slate-700 dark:border-slate-700">
            <div
              v-for="node in hierarchyNodes"
              :key="node.id"
              class="flex items-start justify-between gap-3 px-3 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/40"
              :data-testid="`rights-node-${node.id}`"
            >
              <label class="flex min-w-0 cursor-pointer items-start gap-3">
                <input v-model="selectedNodeIds" type="checkbox" :value="node.id" :disabled="node.blockedByDeny" class="mt-0.5" />
                <span class="min-w-0">
                  <span class="block text-sm text-slate-800 dark:text-slate-100">{{ node.pathLabel }}</span>
                  <span v-if="node.blockedByDeny" class="block text-xs text-amber-600 dark:text-amber-400">
                    {{ $t('settings.users.rights.deniedScopePreserved') }}
                  </span>
                  <span v-if="node.orphaned" class="block text-xs text-amber-600 dark:text-amber-400">
                    {{ $t('settings.users.rights.unknownScope') }}
                  </span>
                </span>
              </label>
              <label
                v-if="selectedNodeIds.includes(node.id)"
                class="flex shrink-0 cursor-pointer items-center gap-2 text-xs text-slate-600 dark:text-slate-300"
              >
                <input
                  v-model="centralControlByNode[node.id]"
                  type="checkbox"
                  :disabled="node.orphaned"
                  :data-testid="`central-control-${node.id}`"
                />
                <span>{{ $t('settings.users.rights.centralControl') }}</span>
              </label>
            </div>
          </div>
          <p class="text-xs text-slate-500">{{ $t('settings.users.rights.selectedScopes', { n: selectedNodeIds.length }) }}</p>
          <div v-if="selectedOrphanCount" class="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300" data-testid="orphaned-scope-block">
            {{ $t('settings.users.rights.orphanedScopeBlock', { n: selectedOrphanCount }) }}
          </div>
          <div v-if="hasNewScopes && !selectedRole" class="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300" data-testid="new-scope-role-required">
            {{ $t('settings.users.rights.newScopeRoleRequired') }}
          </div>
          <div v-if="previewError" class="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-500" data-testid="rights-preview-error">
            {{ previewError }}
          </div>
        </section>

        <section v-else-if="step === 3" class="flex flex-col gap-3" data-testid="rights-preview-step">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h4 class="font-semibold text-slate-800 dark:text-slate-100">{{ $t('settings.users.rights.previewTitle') }}</h4>
              <p class="mt-1 text-sm text-slate-500">{{ $t('settings.users.rights.previewHint') }}</p>
            </div>
            <button v-if="previewTargets.length" type="button" class="btn-secondary btn-sm" :disabled="previewLoading" @click="loadPreview">
              {{ $t('settings.users.rights.refreshPreview') }}
            </button>
          </div>
          <div v-if="!previewTargets.length" class="rounded-lg border border-slate-200 p-4 text-sm text-slate-500 dark:border-slate-700" data-testid="rights-preview-empty">
            {{ $t('settings.users.rights.emptyPreview') }}
          </div>
          <div v-else-if="previewLoading" class="flex justify-center py-8"><Spinner /></div>
          <div v-else-if="previewError" class="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-500" data-testid="rights-preview-error">
            {{ previewError }}
          </div>
          <div v-else class="flex max-h-96 flex-col gap-3 overflow-y-auto">
            <article
              v-for="group in previewGroups"
              :key="group.key"
              class="rounded-lg border border-slate-200 p-3 dark:border-slate-700"
              :data-testid="`preview-target-${group.nodeId}`"
            >
              <div class="mb-2 flex items-center justify-between gap-3">
                <h5 class="text-sm font-medium text-slate-800 dark:text-slate-100">{{ group.pathLabel }}</h5>
                <span
                  v-if="group.centralControl !== null"
                  class="text-xs text-slate-500"
                  :data-testid="`preview-central-control-${group.nodeId}`"
                >
                  {{ group.centralControl ? $t('settings.users.rights.centralControlEnabled') : $t('settings.users.rights.centralControlDisabled') }}
                </span>
              </div>
              <div class="grid gap-2 sm:grid-cols-2">
                <div
                  v-for="result in group.results"
                  :key="result.action"
                  class="rounded-md bg-slate-50 p-2 text-xs dark:bg-slate-800/60"
                  :data-testid="`preview-${group.nodeId}-${result.action}`"
                >
                  <div class="flex items-center justify-between gap-2">
                    <span class="font-medium text-slate-700 dark:text-slate-200">{{ actionLabel(result.action) }}</span>
                    <span :class="result.allowed ? 'text-green-600 dark:text-green-400' : 'text-red-500'">
                      {{ result.allowed ? $t('settings.users.rights.allowed') : $t('settings.users.rights.denied') }}
                    </span>
                  </div>
                  <p class="mt-1 text-slate-500">{{ reasonText(result) }}</p>
                </div>
              </div>
            </article>
          </div>
        </section>

        <section v-else class="flex flex-col gap-3" data-testid="rights-confirm-step">
          <div>
            <h4 class="font-semibold text-slate-800 dark:text-slate-100">{{ $t('settings.users.rights.confirmTitle') }}</h4>
            <p class="mt-1 text-sm text-slate-500">{{ $t('settings.users.rights.confirmHint') }}</p>
          </div>
          <dl class="grid gap-3 rounded-lg border border-slate-200 p-4 text-sm dark:border-slate-700 sm:grid-cols-2">
            <div>
              <dt class="text-xs text-slate-500">{{ $t('settings.users.rights.roleHandling') }}</dt>
              <dd class="font-medium text-slate-800 dark:text-slate-100" data-testid="rights-role-summary">{{ roleSummary }}</dd>
            </div>
            <div>
              <dt class="text-xs text-slate-500">{{ $t('settings.users.rights.selectedAreas') }}</dt>
              <dd class="font-medium text-slate-800 dark:text-slate-100">{{ selectedNodeIds.length }}</dd>
            </div>
          </dl>
          <div v-if="selectedNodeIds.length" class="flex flex-col gap-2" data-testid="central-control-summary">
            <h5 class="text-sm font-medium text-slate-800 dark:text-slate-100">{{ $t('settings.users.rights.centralControlSummary') }}</h5>
            <div
              v-for="nodeId in selectedNodeIds"
              :key="nodeId"
              class="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-slate-700"
              :data-testid="`confirm-central-control-${nodeId}`"
            >
              <span class="min-w-0 truncate text-slate-600 dark:text-slate-300">{{ nodeLabels[nodeId] ?? nodeId }}</span>
              <span class="shrink-0 font-medium text-slate-800 dark:text-slate-100">
                {{ centralControlByNode[nodeId] ? $t('settings.users.rights.centralControlEnabled') : $t('settings.users.rights.centralControlDisabled') }}
              </span>
            </div>
          </div>
          <div v-if="advancedGrants.length" class="flex flex-col gap-2" data-testid="advanced-grants-preserved">
            <div>
              <h5 class="text-sm font-medium text-slate-800 dark:text-slate-100">{{ $t('settings.users.rights.advancedTitle') }}</h5>
              <p class="text-xs text-slate-500">{{ $t('settings.users.rights.advancedPreserved', { n: advancedGrants.length }) }}</p>
            </div>
            <div
              v-for="(grant, index) in advancedGrants"
              :key="`${grant.node_type}:${grant.node_id}:${grant.effect}:${grant.role}`"
              class="flex items-center justify-between gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700"
              :data-testid="`advanced-grant-${index}`"
            >
              <span class="min-w-0 break-all text-xs text-slate-600 dark:text-slate-300">
                {{ $t('settings.users.rights.advancedDescription', { effect: grant.effect, role: grant.role, nodeType: grant.node_type, nodeId: grant.node_id }) }}
              </span>
              <button type="button" class="btn-secondary btn-sm shrink-0" :data-testid="`remove-advanced-grant-${index}`" @click="removeAdvancedGrant(index)">
                {{ $t('settings.users.rights.removeAdvanced') }}
              </button>
            </div>
          </div>
          <div v-if="saveError" class="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-500" data-testid="rights-save-error">
            {{ saveError }}
          </div>
        </section>

        <div class="flex items-center justify-between gap-3 border-t border-slate-200 pt-4 dark:border-slate-700">
          <button type="button" class="btn-secondary" @click="step === 1 ? close() : step--">
            {{ step === 1 ? $t('common.cancel') : $t('settings.users.rights.back') }}
          </button>
          <button
            v-if="step < 4"
            type="button"
            class="btn-primary"
            :disabled="!canContinue || previewLoading"
            data-testid="rights-next"
            @click="continueToNextStep"
          >
            {{ $t('settings.users.rights.next') }}
          </button>
          <button
            v-else
            type="button"
            class="btn-primary"
            :disabled="saving"
            data-testid="rights-save"
            @click="save"
          >
            <Spinner v-if="saving" size="sm" color="white" />
            {{ $t('settings.users.rights.save') }}
          </button>
        </div>
      </template>
    </div>
  </Modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { hierarchyApi } from '@/api/client'
import { authzApi } from '@/api/authz'
import Modal from '@/components/ui/Modal.vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  modelValue: Boolean,
  username: { type: String, required: true },
})
const emit = defineEmits(['update:modelValue', 'saved'])
const { t } = useI18n()

const ACTIONS = ['read', 'write', 'activate', 'generate']
const GRANT_FIELDS = ['node_type', 'node_id', 'role', 'effect', 'central_control']
const KNOWN_REASON_CODES = new Set(['admin', 'allowed', 'direct_datapoint_grant', 'explicit_deny', 'central_control_required', 'missing_allow', 'no_targets'])

const step = ref(1)
const loading = ref(false)
const loadError = ref('')
const selectedRole = ref('')
const selectedNodeIds = ref([])
const originalNodeIds = ref([])
const originalRolesByNode = ref({})
const centralControlByNode = ref({})
const originalAdvancedTargets = ref([])
const hierarchyNodes = ref([])
const advancedGrants = ref([])
const mixedRoles = ref(false)
const reassignExisting = ref(false)
const grantsEtag = ref('')
const previewLoading = ref(false)
const previewError = ref('')
const previewResults = ref([])
const saving = ref(false)
const saveError = ref('')

const steps = computed(() => [
  { number: 1, label: t('settings.users.rights.steps.role') },
  { number: 2, label: t('settings.users.rights.steps.scopes') },
  { number: 3, label: t('settings.users.rights.steps.preview') },
  { number: 4, label: t('settings.users.rights.steps.confirm') },
])

const roleOptions = computed(() => ['guest', 'resident', 'operator', 'owner'].map((value) => ({
  value,
  label: t(`settings.users.rights.roles.${value}.label`),
  description: t(`settings.users.rights.roles.${value}.description`),
})))

const selectedRoleLabel = computed(() => roleOptions.value.find((option) => option.value === selectedRole.value)?.label ?? '')
const selectedOrphanCount = computed(() => hierarchyNodes.value.filter((node) => node.orphaned && selectedNodeIds.value.includes(node.id)).length)
const hasNewScopes = computed(() => selectedNodeIds.value.some((nodeId) => !originalRolesByNode.value[nodeId]))
const previewTargets = computed(() => {
  const targets = [
    ...originalNodeIds.value.map((nodeId) => ({ node_type: 'hierarchy', node_id: nodeId, control_class: 'central_plant' })),
    ...selectedNodeIds.value.map((nodeId) => ({ node_type: 'hierarchy', node_id: nodeId, control_class: 'central_plant' })),
    ...originalAdvancedTargets.value,
    ...advancedGrants.value.map(({ node_type, node_id }) => ({ node_type, node_id: String(node_id) })),
  ]
  const seen = new Set()
  return targets.filter((target) => {
    const key = `${target.node_type}:${target.node_id}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
})
const roleSummary = computed(() => {
  if (reassignExisting.value) return t('settings.users.rights.roleSummaryBulk', { role: selectedRoleLabel.value })
  if (hasNewScopes.value) return t('settings.users.rights.roleSummaryNew', { role: selectedRoleLabel.value })
  return t('settings.users.rights.existingRolesPreserved')
})
const canContinue = computed(() => {
  if (step.value === 1) return true
  if (step.value === 2) {
    return selectedOrphanCount.value === 0
      && (!hasNewScopes.value || !!selectedRole.value)
      && (!reassignExisting.value || !!selectedRole.value)
  }
  if (step.value === 3) return !previewError.value && (!previewTargets.value.length || previewResults.value.length > 0)
  return true
})

const nodeLabels = computed(() => Object.fromEntries(hierarchyNodes.value.map((node) => [node.id, node.pathLabel])))
const previewGroups = computed(() => previewTargets.value.map((target) => ({
  key: `${target.node_type}:${target.node_id}`,
  nodeId: target.node_id,
  pathLabel: target.node_type === 'hierarchy'
    ? nodeLabels.value[target.node_id] ?? target.node_id
    : t('settings.users.rights.advancedTarget', { nodeType: target.node_type, nodeId: target.node_id }),
  centralControl: target.node_type === 'hierarchy' && target.control_class === 'central_plant'
    ? Boolean(centralControlByNode.value[target.node_id])
    : null,
  results: ACTIONS.map((action) => previewResults.value.find((result) => (
    result.node_type === target.node_type && result.node_id === target.node_id && result.action === action
  ))).filter(Boolean),
})))

watch(
  () => props.modelValue,
  (open) => {
    if (open) initialize()
  },
  { immediate: true },
)

function cleanGrant(grant) {
  return Object.fromEntries(GRANT_FIELDS.map((field) => [field, field === 'central_control' ? Boolean(grant[field]) : grant[field]]))
}

function sortGrants(grants) {
  return [...grants].sort((left, right) => (
    GRANT_FIELDS.map((field) => String(left[field]).localeCompare(String(right[field]))).find((result) => result !== 0) ?? 0
  ))
}

function isEditableGrant(grant) {
  return grant.node_type === 'hierarchy' && grant.effect === 'allow'
}

function flattenNodes(nodes, parentId = null, target = []) {
  for (const node of nodes || []) {
    const normalized = { ...node, parent_id: node.parent_id ?? parentId }
    target.push(normalized)
    flattenNodes(node.children, node.id, target)
  }
  return target
}

function nodesWithPaths(tree, rawNodes) {
  const flat = flattenNodes(rawNodes)
  const byId = new Map(flat.map((node) => [node.id, node]))
  return flat.map((node) => {
    const path = []
    let current = node
    let guard = 0
    while (current && guard < 64) {
      path.unshift(current.name)
      current = current.parent_id ? byId.get(current.parent_id) : null
      guard++
    }
    return {
      id: String(node.id),
      pathLabel: [tree.name, ...path].filter(Boolean).join(' › '),
      orphaned: false,
    }
  })
}

async function loadHierarchyNodes() {
  const { data: trees } = await hierarchyApi.listTrees()
  const nodesByTree = await Promise.all((trees || []).map(async (tree) => {
    const { data } = await hierarchyApi.getTreeNodes(tree.id)
    return nodesWithPaths(tree, data)
  }))
  return nodesByTree.flat().sort((a, b) => a.pathLabel.localeCompare(b.pathLabel))
}

async function initialize() {
  step.value = 1
  loading.value = true
  loadError.value = ''
  selectedRole.value = ''
  selectedNodeIds.value = []
  originalNodeIds.value = []
  originalRolesByNode.value = {}
  centralControlByNode.value = {}
  originalAdvancedTargets.value = []
  hierarchyNodes.value = []
  advancedGrants.value = []
  mixedRoles.value = false
  reassignExisting.value = false
  grantsEtag.value = ''
  previewResults.value = []
  previewError.value = ''
  saveError.value = ''
  try {
    const [grantsResponse, loadedNodes] = await Promise.all([
      authzApi.getUserGrants(props.username),
      loadHierarchyNodes(),
    ])
    const { data, headers } = grantsResponse
    grantsEtag.value = headers?.etag ?? headers?.ETag ?? ''
    const grants = (data.grants || []).map(cleanGrant)
    const editable = grants.filter(isEditableGrant)
    advancedGrants.value = grants.filter((grant) => !isEditableGrant(grant))
    originalAdvancedTargets.value = advancedGrants.value.map(({ node_type, node_id }) => ({ node_type, node_id: String(node_id) }))
    selectedNodeIds.value = [...new Set(editable.map((grant) => String(grant.node_id)))]
    originalNodeIds.value = [...selectedNodeIds.value]
    originalRolesByNode.value = Object.fromEntries(editable.map((grant) => [String(grant.node_id), grant.role]))
    centralControlByNode.value = Object.fromEntries(editable.map((grant) => [String(grant.node_id), Boolean(grant.central_control)]))
    const roles = [...new Set(editable.map((grant) => grant.role))]
    mixedRoles.value = roles.length > 1
    selectedRole.value = roles.length === 1 ? roles[0] : ''
    const knownIds = new Set(loadedNodes.map((node) => node.id))
    const orphanedNodes = selectedNodeIds.value
      .filter((nodeId) => !knownIds.has(nodeId))
      .map((nodeId) => ({ id: nodeId, pathLabel: nodeId, orphaned: true }))
    const deniedHierarchyIds = new Set(advancedGrants.value
      .filter((grant) => grant.node_type === 'hierarchy' && grant.effect === 'deny')
      .map((grant) => String(grant.node_id)))
    hierarchyNodes.value = [...loadedNodes, ...orphanedNodes].map((node) => ({
      ...node,
      blockedByDeny: deniedHierarchyIds.has(node.id),
    }))
  } catch (error) {
    loadError.value = error.response?.data?.detail ?? t('settings.users.rights.loadError')
  } finally {
    loading.value = false
  }
}

function editableGrants() {
  return selectedNodeIds.value.map((nodeId) => ({
    node_type: 'hierarchy',
    node_id: nodeId,
    role: originalRolesByNode.value[nodeId] && !reassignExisting.value
      ? originalRolesByNode.value[nodeId]
      : selectedRole.value,
    effect: 'allow',
    central_control: Boolean(centralControlByNode.value[nodeId]),
  }))
}

function replacementGrants() {
  return sortGrants([...advancedGrants.value.map(cleanGrant), ...editableGrants()])
}

function previewBody() {
  const grants = replacementGrants().map((grant) => ({
    principal_type: 'user',
    principal_id: props.username,
    ...grant,
  }))
  return {
    principal: { principal_type: 'user', principal_id: props.username },
    actions: ACTIONS,
    targets: previewTargets.value,
    draft_grants: grants,
    include_persisted: false,
  }
}

async function loadPreview() {
  if (!previewTargets.value.length) {
    previewError.value = ''
    previewResults.value = []
    return
  }
  previewLoading.value = true
  previewError.value = ''
  previewResults.value = []
  try {
    const { data } = await authzApi.preview(previewBody())
    previewResults.value = data.results || []
  } catch (error) {
    previewError.value = error.response?.data?.detail ?? t('settings.users.rights.previewError')
  } finally {
    previewLoading.value = false
  }
}

async function continueToNextStep() {
  if (!canContinue.value) return
  if (step.value === 2) {
    await loadPreview()
    if (previewError.value) return
  }
  step.value++
}

async function save() {
  saving.value = true
  saveError.value = ''
  try {
    const { data } = await authzApi.updateUserGrants(props.username, replacementGrants(), grantsEtag.value)
    emit('saved', data)
    close()
  } catch (error) {
    saveError.value = error.response?.status === 412
      ? t('settings.users.rights.concurrentChange')
      : error.response?.data?.detail ?? t('settings.users.rights.saveError')
  } finally {
    saving.value = false
  }
}

function actionLabel(action) {
  return t(`settings.users.rights.actions.${action}`)
}

function reasonText(result) {
  return KNOWN_REASON_CODES.has(result.reason)
    ? t(`settings.users.rights.reasons.${result.reason}`)
    : result.reason_text
}

async function removeAdvancedGrant(index) {
  advancedGrants.value.splice(index, 1)
  previewResults.value = []
  previewError.value = ''
  step.value = 3
  await loadPreview()
}

function close() {
  emit('update:modelValue', false)
}
</script>
