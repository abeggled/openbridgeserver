<script setup lang="ts">
import { useVisuStore } from '@/stores/visu'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'

const store = useVisuStore()
const { breadcrumb } = storeToRefs(store)
const router = useRouter()
</script>

<template>
  <nav v-if="breadcrumb.length > 0" class="flex items-center gap-1 text-sm text-gray-400 flex-wrap">
    <button @click="router.push({ name: 'tree' })" class="hover:text-gray-200 transition-colors">
      ⌂
    </button>
    <template v-for="(node, idx) in breadcrumb" :key="node.id">
      <span class="text-gray-600">/</span>
      <button
        class="transition-colors truncate max-w-[200px]"
        :class="idx === breadcrumb.length - 1 ? 'text-gray-200 font-medium pointer-events-none' : 'hover:text-gray-200'"
        @click="router.push({ name: 'viewer', params: { id: node.id } })"
      >
        {{ node.name }}
      </button>
    </template>
  </nav>
</template>
