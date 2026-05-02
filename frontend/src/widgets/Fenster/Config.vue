<script setup lang="ts">
import { reactive, computed, watch } from 'vue'
import DataPointPicker from '@/components/DataPointPicker.vue'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive({
  label:                (props.modelValue.label                as string)  ?? '',
  mode:                 (props.modelValue.mode                 as string)  ?? 'fenster',
  dp_contact:           (props.modelValue.dp_contact           as string)  ?? '',
  invert_contact:       (props.modelValue.invert_contact       as boolean) ?? false,
  dp_tilt:              (props.modelValue.dp_tilt              as string)  ?? '',
  invert_tilt:          (props.modelValue.invert_tilt          as boolean) ?? false,
  dp_contact_left:      (props.modelValue.dp_contact_left      as string)  ?? '',
  invert_contact_left:  (props.modelValue.invert_contact_left  as boolean) ?? false,
  dp_tilt_left:         (props.modelValue.dp_tilt_left         as string)  ?? '',
  invert_tilt_left:     (props.modelValue.invert_tilt_left     as boolean) ?? false,
  dp_contact_right:     (props.modelValue.dp_contact_right     as string)  ?? '',
  invert_contact_right: (props.modelValue.invert_contact_right as boolean) ?? false,
  dp_tilt_right:        (props.modelValue.dp_tilt_right        as string)  ?? '',
  invert_tilt_right:    (props.modelValue.invert_tilt_right    as boolean) ?? false,
  dp_position:          (props.modelValue.dp_position          as string)  ?? '',
  dp_position_status:   (props.modelValue.dp_position_status   as string)  ?? '',
  invert_position:      (props.modelValue.invert_position      as boolean) ?? false,
  enable_shutter:       (props.modelValue.enable_shutter       as boolean) ?? false,
  dp_shutter:           (props.modelValue.dp_shutter           as string)  ?? '',
  dp_shutter_status:    (props.modelValue.dp_shutter_status    as string)  ?? '',
  invert_shutter:       (props.modelValue.invert_shutter       as boolean) ?? false,
  handle_left:          (props.modelValue.handle_left          as boolean) ?? true,
  handle_right:         (props.modelValue.handle_right         as boolean) ?? true,
  color_closed:         (props.modelValue.color_closed         as string)  ?? '#16a34a',
  color_tilted:         (props.modelValue.color_tilted         as string)  ?? '#f97316',
  color_open:           (props.modelValue.color_open           as string)  ?? '#ef4444',
})

const isSingleWing  = computed(() => cfg.mode === 'fenster' || cfg.mode === 'fenster_r')
const isDoubleWing  = computed(() => cfg.mode === 'fenster_2' || cfg.mode === 'zweituerer')
const isDoor        = computed(() => cfg.mode === 'tuere' || cfg.mode === 'tuere_r')
const isSlidingDoor = computed(() => cfg.mode === 'schiebetuer' || cfg.mode === 'schiebetuer_r')
const isRoof        = computed(() => cfg.mode === 'dachfenster')

// Dachflächenfenster hat keine Kontakt-/Kippschalter mehr — nur noch Positionswerte
const showContact       = computed(() => isSingleWing.value || isDoor.value || isSlidingDoor.value)
const showTilt          = computed(() => isSingleWing.value)
const showWings         = computed(() => isDoubleWing.value)
const showPosition      = computed(() => isRoof.value)
// Eintürer nutzen denselben Datenpunkt-Satz wie der jeweilige Flügel des Zweitürers
const showEintuerLeft   = computed(() => cfg.mode === 'eintuer_l')
const showEintuerRight  = computed(() => cfg.mode === 'eintuer_r')

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-3">
    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Beschriftung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Wohnzimmer Süd"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Statusfarben -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-2">Statusfarben</label>
      <div class="flex gap-4">
        <div class="flex flex-col items-center gap-1">
          <input type="color" v-model="cfg.color_closed"
                 class="w-8 h-8 rounded cursor-pointer border border-gray-300 dark:border-gray-600 bg-transparent p-0.5" />
          <span class="text-xs text-gray-500 dark:text-gray-400">Geschlossen</span>
        </div>
        <div class="flex flex-col items-center gap-1">
          <input type="color" v-model="cfg.color_tilted"
                 class="w-8 h-8 rounded cursor-pointer border border-gray-300 dark:border-gray-600 bg-transparent p-0.5" />
          <span class="text-xs text-gray-500 dark:text-gray-400">Kipp</span>
        </div>
        <div class="flex flex-col items-center gap-1">
          <input type="color" v-model="cfg.color_open"
                 class="w-8 h-8 rounded cursor-pointer border border-gray-300 dark:border-gray-600 bg-transparent p-0.5" />
          <span class="text-xs text-gray-500 dark:text-gray-400">Offen</span>
        </div>
      </div>
    </div>

    <!-- Typ -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Typ</label>
      <select
        v-model="cfg.mode"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="fenster">Einflügelfenster (links angeschlagen)</option>
        <option value="fenster_r">Einflügelfenster (rechts angeschlagen)</option>
        <option value="fenster_2">Zweiflügelfenster</option>
        <option value="eintuer_l">Eintürer (links angeschlagen)</option>
        <option value="eintuer_r">Eintürer (rechts angeschlagen)</option>
        <option value="zweituerer">Zweitürer</option>
        <option value="schiebetuer">Schiebetüre (fixer Teil links)</option>
        <option value="schiebetuer_r">Schiebetüre (fixer Teil rechts)</option>
        <option value="dachfenster">Dachflächenfenster</option>
        <option value="tuere">Türe (links angeschlagen)</option>
        <option value="tuere_r">Türe (rechts angeschlagen)</option>
      </select>
    </div>

    <hr class="border-gray-200 dark:border-gray-700" />

    <!-- Single / Door / Sliding: main contact -->
    <template v-if="showContact">
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Kontakt</p>
      <DataPointPicker
        v-model="cfg.dp_contact"
        label="Fensterkontakt / Türkontakt (BOOLEAN)"
        :compatible-types="['BOOLEAN']"
      />
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-contact" v-model="cfg.invert_contact" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-contact" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn false = offen
        </label>
      </div>
    </template>

    <!-- Tilt contact (single-wing only) -->
    <template v-if="showTilt">
      <DataPointPicker
        v-model="cfg.dp_tilt"
        label="Kippsensor (optional, BOOLEAN)"
        :compatible-types="['BOOLEAN']"
      />
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-tilt" v-model="cfg.invert_tilt" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-tilt" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn false = gekippt
        </label>
      </div>
    </template>

    <!-- Double-wing contacts -->
    <template v-if="showWings">
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Darstellung</p>
      <div class="flex items-center gap-2 pl-1">
        <input id="handle-left" v-model="cfg.handle_left" type="checkbox" class="rounded accent-blue-500" />
        <label for="handle-left" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Griff linker Flügel anzeigen
        </label>
      </div>
      <div class="flex items-center gap-2 pl-1">
        <input id="handle-right" v-model="cfg.handle_right" type="checkbox" class="rounded accent-blue-500" />
        <label for="handle-right" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Griff rechter Flügel anzeigen
        </label>
      </div>

      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Linker Flügel</p>
      <DataPointPicker
        v-model="cfg.dp_contact_left"
        label="Kontakt links (BOOLEAN)"
        :compatible-types="['BOOLEAN']"
      />
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-contact-left" v-model="cfg.invert_contact_left" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-contact-left" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn false = offen
        </label>
      </div>
      <DataPointPicker
        v-model="cfg.dp_tilt_left"
        label="Kippsensor links (optional, BOOLEAN)"
        :compatible-types="['BOOLEAN']"
      />
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-tilt-left" v-model="cfg.invert_tilt_left" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-tilt-left" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn false = gekippt
        </label>
      </div>

      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Rechter Flügel</p>
      <DataPointPicker
        v-model="cfg.dp_contact_right"
        label="Kontakt rechts (BOOLEAN)"
        :compatible-types="['BOOLEAN']"
      />
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-contact-right" v-model="cfg.invert_contact_right" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-contact-right" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn false = offen
        </label>
      </div>
      <DataPointPicker
        v-model="cfg.dp_tilt_right"
        label="Kippsensor rechts (optional, BOOLEAN)"
        :compatible-types="['BOOLEAN']"
      />
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-tilt-right" v-model="cfg.invert_tilt_right" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-tilt-right" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn false = gekippt
        </label>
      </div>
    </template>

    <!-- Eintürer links angeschlagen — nutzt dp_contact_left / dp_tilt_left (= linker Flügel des Zweitürers) -->
    <template v-if="showEintuerLeft">
      <div class="flex items-center gap-2 pl-1">
        <input id="handle-left" v-model="cfg.handle_left" type="checkbox" class="rounded accent-blue-500" />
        <label for="handle-left" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Griff anzeigen
        </label>
      </div>
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Kontakt</p>
      <DataPointPicker v-model="cfg.dp_contact_left" label="Türkontakt (BOOLEAN)" :compatible-types="['BOOLEAN']"/>
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-contact-left" v-model="cfg.invert_contact_left" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-contact-left" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn false = offen
        </label>
      </div>
      <DataPointPicker v-model="cfg.dp_tilt_left" label="Kippsensor (optional, BOOLEAN)" :compatible-types="['BOOLEAN']"/>
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-tilt-left" v-model="cfg.invert_tilt_left" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-tilt-left" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn false = gekippt
        </label>
      </div>
    </template>

    <!-- Eintürer rechts angeschlagen — nutzt dp_contact_right / dp_tilt_right (= rechter Flügel des Zweitürers) -->
    <template v-if="showEintuerRight">
      <div class="flex items-center gap-2 pl-1">
        <input id="handle-right" v-model="cfg.handle_right" type="checkbox" class="rounded accent-blue-500" />
        <label for="handle-right" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Griff anzeigen
        </label>
      </div>
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Kontakt</p>
      <DataPointPicker v-model="cfg.dp_contact_right" label="Türkontakt (BOOLEAN)" :compatible-types="['BOOLEAN']"/>
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-contact-right" v-model="cfg.invert_contact_right" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-contact-right" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn false = offen
        </label>
      </div>
      <DataPointPicker v-model="cfg.dp_tilt_right" label="Kippsensor (optional, BOOLEAN)" :compatible-types="['BOOLEAN']"/>
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-tilt-right" v-model="cfg.invert_tilt_right" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-tilt-right" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn false = gekippt
        </label>
      </div>
    </template>

    <!-- Dachflächenfenster: Fensterposition + optionaler Rollladen -->
    <template v-if="showPosition">
      <hr class="border-gray-200 dark:border-gray-700" />
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Fensterposition</p>
      <DataPointPicker
        v-model="cfg.dp_position"
        label="Fensterposition Senden (0 = zu, 100 = offen)"
        :compatible-types="['FLOAT', 'INTEGER']"
      />
      <DataPointPicker
        v-model="cfg.dp_position_status"
        label="Fensterposition Status / Anzeige (0 = zu, 100 = offen)"
        :compatible-types="['FLOAT', 'INTEGER']"
      />
      <div class="flex items-center gap-2 pl-1">
        <input id="inv-position" v-model="cfg.invert_position" type="checkbox" class="rounded accent-blue-500" />
        <label for="inv-position" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
          Invertieren — aktivieren wenn 0 = offen, 100 = zu
        </label>
      </div>

      <hr class="border-gray-200 dark:border-gray-700" />

      <!-- Rollladensteuerung (optional) -->
      <div class="flex items-center gap-2">
        <input id="enable-shutter" v-model="cfg.enable_shutter" type="checkbox" class="rounded accent-blue-500" />
        <label for="enable-shutter" class="text-xs font-medium text-gray-600 dark:text-gray-400 cursor-pointer">
          Rollladensteuerung aktivieren
        </label>
      </div>
      <template v-if="cfg.enable_shutter">
        <DataPointPicker
          v-model="cfg.dp_shutter"
          label="Rollladenposition Senden (0 = offen, 100 = geschlossen)"
          :compatible-types="['FLOAT', 'INTEGER']"
        />
        <DataPointPicker
          v-model="cfg.dp_shutter_status"
          label="Rollladenposition Status / Anzeige"
          :compatible-types="['FLOAT', 'INTEGER']"
        />
        <div class="flex items-center gap-2 pl-1">
          <input id="inv-shutter" v-model="cfg.invert_shutter" type="checkbox" class="rounded accent-blue-500" />
          <label for="inv-shutter" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
            Invertieren — aktivieren wenn 0 = offen, 100 = geschlossen
          </label>
        </div>
      </template>
    </template>
  </div>
</template>
