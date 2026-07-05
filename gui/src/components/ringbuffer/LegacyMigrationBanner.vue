<template>
  <!--
    Hinweis-Balken des Migrations-Assistenten (#966). Erscheint nur, solange
    die Entscheidung ``pending`` ist UND die Legacy-Quelle existiert – und nur
    für Admins (der Migration-Endpoint ist admin-only, Nicht-Admins bekämen
    403). Eskalations-Variante (amber) sobald das Budget die Alt-Historie
    erzwingt oder die Prognose < 7 Tage meldet.
  -->
  <div
    v-if="visible"
    :class="[
      'rounded-lg border px-4 py-3 text-sm shadow-sm',
      escalated
        ? 'border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200'
        : 'border-blue-500/30 bg-blue-500/10 text-blue-800 dark:text-blue-200',
    ]"
    data-testid="legacy-migration-banner"
    :data-escalated="escalated ? 'true' : 'false'"
    role="status"
  >
    <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p class="font-semibold">{{ $t('ringbuffer.migration.bannerTitle') }}</p>
        <p v-if="!compact" class="mt-0.5 text-xs opacity-80">
          {{ escalated ? $t('ringbuffer.migration.bannerEscalated') : $t('ringbuffer.migration.bannerBody') }}
        </p>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <button type="button" class="btn-primary btn-sm" data-testid="legacy-banner-open" @click="$emit('open')">
          {{ $t('ringbuffer.migration.openAssistant') }}
        </button>
        <button type="button" class="btn-secondary btn-sm" data-testid="legacy-banner-later" :disabled="skipping" @click="onLater">
          {{ $t('ringbuffer.migration.later') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useLegacyMigration } from '@/composables/useLegacyMigration'

defineProps({
  // Kompakt-Variante für die Dashboard-Karte: nur Titel + Buttons, kein Fließtext.
  compact: { type: Boolean, default: false },
})
defineEmits(['open'])

// Admin-Gating wie in RingBufferCard (#938): der Banner und die dahinter
// liegende Migration-API sind admin-only.
const auth = useAuthStore()
const { showBanner, escalated, refresh, decide } = useLegacyMigration()

const skipping = ref(false)
const visible = computed(() => auth.isAdmin && showBanner.value)

onMounted(() => {
  if (auth.isAdmin) {
    refresh().catch(() => {
      // Kein Status ladbar → kein Banner; Fehlerdetails zeigt der Wizard.
    })
  }
})

// „Später" = Entscheidung ``skip``: Alt-Historie bleibt retention-geschützt,
// der Banner verschwindet über den geteilten Status (revidierbar).
async function onLater() {
  skipping.value = true
  try {
    await decide('skip')
  } catch {
    // Fehlgeschlagen → Banner bleibt sichtbar, erneuter Versuch möglich.
  } finally {
    skipping.value = false
  }
}
</script>
