<template>
  <!-- #1296: says so when group addresses fall back to the internal three-level notation -->
  <p v-if="knxProject.styleUnavailable" class="text-xs text-amber-600 dark:text-amber-400" role="status" data-testid="ga-style-notice">
    {{ $t('common.gaStyleUnavailable') }}
    <button type="button" class="ml-1 underline hover:no-underline" @click="knxProject.load({ force: true })">{{ $t('common.gaStyleRetry') }}</button>
  </p>
</template>

<script setup>
import { useKnxProjectStore } from '@/stores/knxProject'

const knxProject = useKnxProjectStore()
</script>
