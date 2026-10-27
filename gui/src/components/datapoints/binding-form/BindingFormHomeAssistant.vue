<template>
  <div class="section-header">{{ $t('adapters.bindingForm.haSection') }}</div>
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.haEntityIdLabel') }}</label>
    <SuggestInput
      :empty-text="$t('adapters.bindingForm.haNoSuggestions')"
      v-model="cfg.entity_id"
      :fetch-suggestions="suggestEntities"
      :placeholder="$t('adapters.bindingForm.haEntityIdPlaceholder')"
      testid="config-field-entity_id"
      input-class="font-mono text-sm"
      required
    />
    <p class="hint">{{ $t(selectedInstanceId ? 'adapters.bindingForm.haEntityIdSearchHint' : 'adapters.bindingForm.haEntityIdHint') }}</p>
  </div>
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.haAttributeLabel') }} <span class="optional">{{ $t('adapters.bindingForm.haAttributeOptional') }}</span></label>
    <SuggestInput
      :empty-text="$t('adapters.bindingForm.haNoSuggestions')"
      v-model="cfg.attribute"
      :fetch-suggestions="suggestAttributes"
      :placeholder="$t('adapters.bindingForm.haAttributePlaceholder')"
      testid="config-field-attribute"
      input-class="font-mono text-sm"
    />
    <p class="hint">{{ $t('adapters.bindingForm.haAttributeHint') }}</p>
  </div>

  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.haProfileLabel') }} <span class="optional">{{ $t('adapters.bindingForm.haProfileOptional') }}</span></label>
    <select v-model="cfg.profile" class="input" data-testid="config-field-profile">
      <option value="">{{ $t('adapters.bindingForm.haProfileNone') }}</option>
      <option v-if="unknownProfile" :value="cfg.profile">{{ cfg.profile }}</option>
      <option v-for="p in profiles" :key="p.id" :value="p.id">{{ $t(profileLabelKey(p.id)) }}</option>
    </select>
    <p class="hint">{{ $t('adapters.bindingForm.haProfileHint') }}</p>
    <p v-if="typeMismatch" class="text-xs text-amber-500" data-testid="ha-type-mismatch">
      {{ $t('adapters.bindingForm.haProfileTypeMismatch', typeMismatch) }}
    </p>
  </div>

  <div class="form-group" style="max-width:280px">
    <label class="label">{{ $t('adapters.bindingForm.haReadAsLabel') }}</label>
    <select v-model="cfg.read_as" class="input" data-testid="config-field-read_as">
      <option value="">{{ $t('adapters.bindingForm.haReadAsDefault') }}</option>
      <option v-for="kind in READ_AS" :key="kind" :value="kind">{{ $t(`adapters.bindingForm.haReadAs.${kind}`) }}</option>
    </select>
    <p class="hint">{{ $t('adapters.bindingForm.haReadAsHint') }}</p>
  </div>

  <div class="rounded-lg border border-slate-200 dark:border-slate-700 p-3 text-xs flex flex-col gap-1" data-testid="ha-resolved-mapping">
    <div class="font-semibold text-slate-600 dark:text-slate-300">{{ $t('adapters.bindingForm.haResolvedMapping', { mode: $t(`adapters.homeassistant.mode.${resolved.mode}`) }) }}</div>
    <div v-if="resolved.convention" class="text-blue-500" data-testid="ha-convention">{{ $t(conventionKey(resolved.convention)) }}</div>
    <div class="text-slate-500">
      {{ $t('adapters.homeassistant.mappingRead', { source: resolved.read.attribute || 'state' }) }}
      <span v-if="resolved.read.state_map" class="font-mono">{{ JSON.stringify(resolved.read.state_map) }}</span>
    </div>
    <div v-if="resolved.read.transitional_states.length" class="text-slate-500">
      {{ $t('adapters.homeassistant.mappingTransitional', { states: resolved.read.transitional_states.join(', ') }) }}
    </div>
    <div v-if="resolved.read.fault_states.length" class="text-slate-500">
      {{ $t('adapters.homeassistant.mappingFault', { states: resolved.read.fault_states.join(', ') }) }}
    </div>
    <template v-if="resolved.mode === 'legacy' || resolved.mode === 'default'">
      <div class="text-slate-500" data-testid="ha-write-legacy">{{ $t(`adapters.homeassistant.write.${resolved.mode}`) }}</div>
    </template>
    <div v-else-if="!rows.length" class="text-slate-500">{{ $t('adapters.homeassistant.write.none') }}</div>
    <div v-for="row in rows" :key="row.when ?? 'all'" class="font-mono text-slate-600 dark:text-slate-300" data-testid="ha-write-row">
      {{ $t(row.when === null ? 'adapters.homeassistant.writeAlways' : 'adapters.homeassistant.writeWhen', { when: row.when, action: row.action }) }}
      <span v-if="row.valuePath">{{ $t('adapters.homeassistant.writeValuePath', { path: row.valuePath }) }}</span>
    </div>
    <div v-if="resolved.mode === 'legacy' && cfg.profile" class="text-amber-500" data-testid="ha-legacy-overrides">{{ $t('adapters.bindingForm.haLegacyOverridesProfile') }}</div>
  </div>

  <div class="optional-divider">{{ $t('adapters.bindingForm.haWriteSection') }}</div>
  <p class="hint">{{ $t('adapters.bindingForm.haLegacyHint') }}</p>
  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.haServiceDomainLabel') }} <span class="optional">{{ $t('adapters.bindingForm.haServiceDomainOptional') }}</span></label>
      <SuggestInput
      :empty-text="$t('adapters.bindingForm.haNoSuggestions')"
        v-model="cfg.service_domain"
        :fetch-suggestions="suggestDomains"
        :placeholder="$t('adapters.bindingForm.haServiceDomainPlaceholder')"
        testid="config-field-service_domain"
        input-class="font-mono text-sm"
      />
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.haServiceNameLabel') }} <span class="optional">{{ $t('adapters.bindingForm.haServiceNameOptional') }}</span></label>
      <SuggestInput
      :empty-text="$t('adapters.bindingForm.haNoSuggestions')"
        v-model="cfg.service_name"
        :fetch-suggestions="suggestServices"
        :placeholder="$t('adapters.bindingForm.haServiceNamePlaceholder')"
        testid="config-field-service_name"
        input-class="font-mono text-sm"
      />
    </div>
  </div>
  <div class="form-group" style="max-width:280px">
    <label class="label">{{ $t('adapters.bindingForm.haServiceDataKeyLabel') }} <span class="optional">{{ $t('adapters.bindingForm.haServiceDataKeyOptional') }}</span></label>
    <SuggestInput
      :empty-text="$t('adapters.bindingForm.haNoSuggestions')"
      v-model="cfg.service_data_key"
      :fetch-suggestions="suggestDataKeys"
      :placeholder="$t('adapters.bindingForm.haServiceDataKeyPlaceholder')"
      testid="config-field-service_data_key"
      input-class="font-mono text-sm"
    />
    <p class="hint">{{ $t('adapters.bindingForm.haServiceDataKeyHint') }}</p>
  </div>

  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.haAdvancedLabel') }} <span class="optional">{{ $t('adapters.bindingForm.haAdvancedOptional') }}</span></label>
    <textarea
      v-model="advancedText"
      class="input font-mono text-xs"
      rows="5"
      spellcheck="false"
      data-testid="config-field-ha-advanced"
      @blur="applyAdvanced"
    />
    <p v-if="advancedInvalid" class="text-xs text-red-400" data-testid="ha-advanced-invalid">{{ $t('adapters.bindingForm.haAdvancedInvalid') }}</p>
    <p class="hint">{{ $t('adapters.bindingForm.haAdvancedHint') }}</p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { adapterApi } from '@/api/client'
import SuggestInput from '@/components/ui/SuggestInput.vue'
import {
  advancedMappingText,
  attributeSuggestions,
  conventionKey,
  domainSuggestions,
  entityDomain,
  entitySuggestions,
  fieldSuggestions,
  serviceSuggestions,
  parseAdvancedMapping,
  profileLabelKey,
  profileTypeMismatch,
  profilesForEntity,
  resolveHaMapping,
  writeRows,
} from '@/utils/haMapping'

const props = defineProps({
  cfg: { type: Object, required: true },
  selectedInstanceId: { type: String, default: '' },
  dpDataType: { type: String, default: '' },
})

const emit = defineEmits(['advanced-validity'])

const READ_AS = ['auto', 'boolean', 'number', 'string']
const COMPLETE_ENTITY_ID = /^[a-z0-9_]+\.[a-z0-9_]+$/
const catalog = ref([])
const advancedText = ref(advancedMappingText(props.cfg))
const advancedInvalid = ref(false)

const profiles = computed(() => profilesForEntity(catalog.value, props.cfg.entity_id))
const unknownProfile = computed(() => !!props.cfg.profile && !profiles.value.some(p => p.id === props.cfg.profile))
const resolved = computed(() => resolveHaMapping(props.cfg, catalog.value))
const rows = computed(() => writeRows(resolved.value.write, resolved.value.writeKind))
const typeMismatch = computed(() => profileTypeMismatch(catalog.value, props.cfg.profile, props.dpDataType, props.cfg.attribute))

// Live suggestions from the selected instance; without one (or offline) the
// fields stay plain free-text inputs.
let servicesPromise = null
let servicesFor = null
const entityCache = new Map()  // `${instance}|${entity_id}` → entity, so a switched instance never answers

async function searchEntities(query, limit) {
  if (!props.selectedInstanceId) return []
  const { data } = await adapterApi.haEntities(props.selectedInstanceId, { q: query, limit })
  for (const item of data.items) entityCache.set(`${props.selectedInstanceId}|${item.entity_id}`, item)
  return data.items
}

async function suggestEntities(query) {
  return entitySuggestions(await searchEntities(query, 30))
}

async function suggestAttributes(query) {
  const id = props.cfg.entity_id
  if (!id) return []
  const key = `${props.selectedInstanceId}|${id}`
  if (!entityCache.has(key)) await searchEntities(id, 10)
  return attributeSuggestions(entityCache.get(key), query)
}

function loadServices() {
  if (!props.selectedInstanceId) return Promise.resolve([])
  if (servicesFor !== props.selectedInstanceId) {
    servicesFor = props.selectedInstanceId
    servicesPromise = adapterApi.haServices(servicesFor).then(r => r.data).catch(() => {
      servicesFor = null
      return []
    })
  }
  return servicesPromise
}

const actionDomain = () => props.cfg.service_domain || entityDomain(props.cfg.entity_id)

async function suggestDomains(query) {
  return domainSuggestions(await loadServices(), query)
}

async function suggestServices(query) {
  return serviceSuggestions(await loadServices(), actionDomain(), query)
}

async function suggestDataKeys(query) {
  return fieldSuggestions(await loadServices(), actionDomain(), props.cfg.service_name, query)
}

function applyAdvanced() {
  const parsed = parseAdvancedMapping(advancedText.value)
  advancedInvalid.value = !parsed.ok
  if (parsed.ok) Object.assign(props.cfg, parsed.value)
  emit('advanced-validity', parsed.ok)  // the parent blocks saving while it is invalid
}

// A profile only fits its own domain(s): drop it when the entity moves to another one,
// instead of letting the save fail with a 422. Only complete entity ids count — while
// the id is being retyped, its partial domain says nothing.
watch(() => String(props.cfg.entity_id ?? '').trim().toLowerCase(), (entityId) => {
  if (!COMPLETE_ENTITY_ID.test(entityId)) return
  const profile = catalog.value.find(p => p.id === props.cfg.profile)
  if (profile && !profile.domains.includes(entityDomain(entityId))) props.cfg.profile = ''
})

onMounted(async () => {
  // A fresh form shows the stored, valid mapping: drop a stale "invalid" from an earlier one.
  emit('advanced-validity', true)
  try {
    const { data } = await adapterApi.haProfiles()
    catalog.value = data
  } catch {
    catalog.value = []  // profiles are optional: the manual fields keep working
  }
})
</script>
