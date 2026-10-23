<script setup lang="ts">
/**
 * Visu-Alarme im Vordergrund — über jeder Seite und der Übersicht.
 *
 * Zeigt den neuesten aktiven Alarm, der für dieses Gerät gilt (Geräteprofil
 * oder „alle“), als Meldungsfenster oder als eingebettete Visu-Seite. Weitere
 * Alarme stehen dahinter an. Quittieren beendet den Alarm auf allen Geräten;
 * Alarme ohne Quittieren-Knopf enden, sobald ihre Bedingung wegfällt.
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { visuAlarms, type ActiveVisuAlarm } from '@/api/client'
import { useVisuAlarms } from '@/composables/useVisuAlarms'
import { useDeviceStore } from '@/stores/device'
import { playAlarmTone } from '@/utils/alarmTone'
import VisuIcon from '@/components/VisuIcon.vue'

const { t } = useI18n()
const device = useDeviceStore()
const { alarms } = useVisuAlarms()

const SEVERITY_CLASS: Record<ActiveVisuAlarm['severity'], string> = {
  info: 'border-blue-500',
  warning: 'border-orange-500',
  critical: 'border-red-600',
}

const forThisDevice = computed(() =>
  alarms.value.filter(a => a.devices.length === 0 || (device.slug !== null && a.devices.includes(device.slug))),
)
const current = computed(() => forThisDevice.value[0] ?? null)
const waiting = computed(() => forThisDevice.value.length - 1)
const hasWaiting = computed(() => forThisDevice.value.length > 1)
const acknowledging = ref(false)
const failed = ref(false)

// Ton nur für neu hinzugekommene Alarme, nicht bei jeder Listen-Aktualisierung.
const announced = new Set<string>()
watch(forThisDevice, (list) => {
  for (const alarm of list) {
    if (announced.has(alarm.id)) continue
    announced.add(alarm.id)
    if (alarm.sound) playAlarmTone()
  }
  for (const id of [...announced]) {
    if (!list.some(a => a.id === id)) announced.delete(id)
  }
}, { immediate: true })

async function acknowledge(alarm: ActiveVisuAlarm) {
  acknowledging.value = true
  failed.value = false
  try {
    await visuAlarms.acknowledge(alarm.id)
  } catch {
    // Schon von einem anderen Gerät quittiert oder nicht erreichbar — die Liste korrigiert sich selbst.
    failed.value = true
  } finally {
    acknowledging.value = false
  }
}

const pageSrc = computed(() => (current.value?.page_id ? `/visu/${encodeURIComponent(current.value.page_id)}?embed=1` : ''))
</script>

<template>
  <Teleport to="body">
    <div
      v-if="current"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4"
      data-testid="alarm-overlay"
      role="alertdialog"
      :aria-label="current.title || current.name"
    >
      <!-- Meldungsfenster -->
      <div
        v-if="current.display === 'message'"
        class="w-full max-w-md rounded-2xl border-t-8 bg-white dark:bg-gray-900 shadow-2xl p-6 flex flex-col items-center gap-3 text-center"
        :class="SEVERITY_CLASS[current.severity]"
        data-testid="alarm-message"
      >
        <span v-if="current.icon" class="text-7xl leading-none" data-testid="alarm-icon"><VisuIcon :icon="current.icon" :preserve-color="true" /></span>
        <h2 v-if="current.title" class="text-2xl font-semibold text-gray-900 dark:text-gray-100" data-testid="alarm-title">{{ current.title }}</h2>
        <p v-if="current.text" class="text-gray-600 dark:text-gray-300 whitespace-pre-line" data-testid="alarm-text">{{ current.text }}</p>
        <button
          v-if="current.ack_button"
          type="button"
          class="mt-2 px-6 py-3 rounded-xl bg-blue-600 hover:bg-blue-700 text-white text-lg font-medium disabled:opacity-60"
          :disabled="acknowledging"
          data-testid="alarm-acknowledge"
          @click="acknowledge(current)"
        >{{ t('alarm.acknowledge') }}</button>
        <p v-if="failed" class="text-xs text-red-500" data-testid="alarm-failed">{{ t('alarm.acknowledgeFailed') }}</p>
        <p v-if="hasWaiting" class="text-xs text-gray-400" data-testid="alarm-waiting">{{ t('alarm.waiting', { n: waiting }) }}</p>
      </div>

      <!-- Visu-Seite als Popup -->
      <div
        v-else
        class="w-full h-full max-w-5xl max-h-[85vh] rounded-2xl border-t-8 bg-white dark:bg-gray-900 shadow-2xl flex flex-col overflow-hidden"
        :class="SEVERITY_CLASS[current.severity]"
        data-testid="alarm-page"
      >
        <div class="flex items-center justify-between gap-3 px-4 py-2 border-b border-gray-200 dark:border-gray-800">
          <span class="font-semibold text-gray-800 dark:text-gray-100 truncate">{{ current.title || current.name }}</span>
          <div class="flex items-center gap-3 shrink-0">
            <span v-if="hasWaiting" class="text-xs text-gray-400" data-testid="alarm-waiting">{{ t('alarm.waiting', { n: waiting }) }}</span>
            <span v-if="failed" class="text-xs text-red-500" data-testid="alarm-failed">{{ t('alarm.acknowledgeFailed') }}</span>
            <button
              v-if="current.ack_button"
              type="button"
              class="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium disabled:opacity-60"
              :disabled="acknowledging"
              data-testid="alarm-acknowledge"
              @click="acknowledge(current)"
            >{{ t('alarm.acknowledge') }}</button>
          </div>
        </div>
        <iframe :key="current.id" :src="pageSrc" class="flex-1 w-full border-0" :title="current.name" data-testid="alarm-frame" />
      </div>
    </div>
  </Teleport>
</template>
