<script setup>
// Variable issues reported by the server for the block's last run (issue #1301).
import { useI18n } from 'vue-i18n'
import { translateVariableIssue } from '@/utils/logicVariables'

defineProps({ issues: { type: Array, default: () => [] } })
const { t } = useI18n()
</script>

<template>
  <ul v-if="issues.length" class="text-xs text-amber-400/80 list-disc pl-4" data-testid="extractor-issues">
    <li v-for="issue in issues" :key="issue">{{ translateVariableIssue(issue, t) }}</li>
  </ul>
</template>
