<template>
  <div class="gn-root" @mouseenter="hovered = true" @mouseleave="hovered = false">

    <!-- Input handles (LEFT) — z-index via inline style -->
    <Handle
      v-for="(inp, i) in def.inputs" :key="'in-' + inp.id"
      type="target" :id="inp.id" :position="Position.Left"
      :style="hStyle(i, def.inputs.length)"
    />

    <!-- Card — height controlled so handles align with rows -->
    <div class="gn-card"
         :style="{ borderTopColor: def.color, background: def.color + '12', minHeight: cardH + 'px' }">

      <div class="gn-header" :style="{ background: def.color + '28' }">
        <span class="gn-title">{{ data.label || def.label }}</span>
        <button class="gn-del nodrag" :style="{ visibility: hovered ? 'visible' : 'hidden' }" @click.stop="remove">✕</button>
      </div>

      <div class="gn-body">
        <div v-if="summary" class="gn-summary">{{ summary }}</div>

        <!-- Port rows — height matches handle spacing -->
        <div class="gn-ports-rows">
          <div
            v-for="r in portRows" :key="r"
            class="gn-port-row"
            :style="{ height: PORT_H + 'px' }"
          >
            <!-- Input label — clickable negation toggle for gate nodes -->
            <button
              v-if="isGateNode && def.inputs[r]"
              class="gn-port-negate nodrag"
              :class="{ 'gn-port-negate--active': !!data[`negate_${def.inputs[r].id}`] }"
              :title="`${def.inputs[r].id} negieren`"
              @click.stop="toggleNegate(def.inputs[r].id)"
            >{{ data[`negate_${def.inputs[r].id}`] ? `¬${def.inputs[r].label}` : def.inputs[r].label }}</button>
            <span v-else class="gn-port-left">{{ def.inputs[r]?.label }}</span>

            <!-- Output label — clickable negation toggle for gate nodes (only on last row) -->
            <button
              v-if="isGateNode && def.outputs[r]"
              class="gn-port-negate gn-port-negate--right nodrag"
              :class="{ 'gn-port-negate--active': !!data.negate_out }"
              title="Ausgang negieren"
              @click.stop="toggleNegate('out')"
            >{{ data.negate_out ? `¬${def.outputs[r].label}` : def.outputs[r].label }}</button>
            <span v-else class="gn-port-right">{{ def.outputs[r]?.label }}</span>
          </div>
        </div>
      </div>

      <!-- Debug value strip -->
      <div v-if="data._dbg" class="gn-debug" data-testid="debug-band">{{ data._dbg }}</div>
    </div>

    <!-- Output handles (RIGHT) -->
    <Handle
      v-for="(out, i) in def.outputs" :key="'out-' + out.id"
      type="source" :id="out.id" :position="Position.Right"
      :style="hStyle(i, def.outputs.length)"
      class="gn-out"
    />

  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Handle, Position, useVueFlow } from '@vue-flow/core'

const { updateNodeData } = useVueFlow()

const props = defineProps({
  id:   { type: String, required: true },
  type: { type: String, required: true },
  data: { type: Object, default: () => ({}) },
})

// ── Node definitions ───────────────────────────────────────────────────────
const NODE_DEFS = {
  const_value:  { label: 'Festwert',    color: '#475569', inputs: [],                                                outputs: [{id:'value', label:'Wert'}]       },
  and:          { label: 'AND',         color: '#1d4ed8', inputs: [{id:'in1',label:'IN 1'},{id:'in2',label:'IN 2'}], outputs: [{id:'out',   label:'Out'}]       },
  or:           { label: 'OR',          color: '#1d4ed8', inputs: [{id:'in1',label:'IN 1'},{id:'in2',label:'IN 2'}], outputs: [{id:'out',   label:'Out'}]       },
  not:          { label: 'NOT',         color: '#1d4ed8', inputs: [{id:'in1',label:'IN 1'}],                         outputs: [{id:'out',   label:'Out'}]       },
  xor:          { label: 'XOR',         color: '#1d4ed8', inputs: [{id:'in1',label:'IN 1'},{id:'in2',label:'IN 2'}], outputs: [{id:'out',   label:'Out'}]       },
  gate:         { label: 'TOR',         color: '#1d4ed8', inputs: [{id:'in',label:'Eingang'},{id:'enable',label:'Freigabe'}], outputs: [{id:'out', label:'Ausgang'}]  },
  compare:      { label: 'Vergleich',   color: '#1d4ed8', inputs: [{id:'in1',label:'IN 1'},{id:'in2',label:'IN 2'}], outputs: [{id:'out',   label:'Erg.'}]      },
  hysteresis:   { label: 'Hysterese',   color: '#1d4ed8', inputs: [{id:'value',label:'Wert'}],                       outputs: [{id:'out',   label:'Out'}]       },
  math_formula: { label: 'Formel',      color: '#7c3aed', inputs: [{id:'in1',label:'IN 1'},{id:'in2',label:'IN 2'}],  outputs: [{id:'result',label:'Erg.'}]      },
  math_map:     { label: 'Skalieren',   color: '#7c3aed', inputs: [{id:'value',label:'Wert'}],                       outputs: [{id:'result',label:'Erg.'}]      },
  timer_delay:  { label: 'Verzögerung', color: '#b45309', inputs: [{id:'trigger',label:'Trigger'}],                  outputs: [{id:'trigger',label:'Trigger'}]  },
  timer_pulse:  { label: 'Impuls',      color: '#b45309', inputs: [{id:'trigger',label:'Trigger'}],                  outputs: [{id:'out',   label:'Out'}]       },
  timer_cron:   { label: 'Trigger',     color: '#b45309', inputs: [],                                                outputs: [{id:'trigger',label:'Trigger'}]  },
  mcp_tool:     { label: 'MCP Tool',    color: '#0e7490', inputs: [{id:'trigger',label:'Trigger'},{id:'input', label:'Input'}], outputs: [{id:'result',label:'Erg.'},{id:'done',label:'Fertig'}] },
  python_script:{ label: 'Python',      color: '#be185d', inputs: [{id:'in1',label:'IN 1'},{id:'in2',label:'IN 2'},{id:'in3',label:'IN 3'}], outputs: [{id:'result',label:'Erg.'}] },
  // Astro
  astro_sun:          { label: 'Astro Sonne',    color: '#d97706', inputs: [],                                                                         outputs: [{id:'sunrise',label:'Aufgang'},{id:'sunset',label:'Untergang'},{id:'is_day',label:'Tagsüber'}] },
  // Math (extended)
  clamp:              { label: 'Begrenzer',      color: '#7c3aed', inputs: [{id:'value',label:'Wert'}],                                                outputs: [{id:'result',label:'Erg.'}]      },
  random_value:       { label: 'Zufallswert',    color: '#7c3aed', inputs: [{id:'trigger',label:'Trigger'}],                                           outputs: [{id:'value', label:'Wert'}]      },
  statistics:         { label: 'Statistik',      color: '#7c3aed', inputs: [{id:'value',label:'Wert'},{id:'reset',label:'Reset'}],                      outputs: [{id:'min',label:'Min'},{id:'max',label:'Max'},{id:'avg',label:'∅'},{id:'count',label:'N'}] },
  heating_circuit:    { label: 'Sommer/Winter', color: '#7c3aed', inputs: [{id:'value',label:'Temp °C'}], outputs: [{id:'heating_mode',label:'Heizmodus'},{id:'daily_avg',label:'Tagesmittel'},{id:'monthly_avg',label:'Monatsmittel'},{id:'t1',label:'T1 (dbg)'},{id:'t2',label:'T2 (dbg)'},{id:'t3',label:'T3 (dbg)'}] },
  min_max_tracker:    { label: 'Min/Max',        color: '#7c3aed', inputs: [{id:'value',label:'Wert'}],                                                outputs: [{id:'min_daily',label:'Min/d'},{id:'max_daily',label:'Max/d'},{id:'min_weekly',label:'Min/w'},{id:'max_weekly',label:'Max/w'},{id:'min_monthly',label:'Min/m'},{id:'max_monthly',label:'Max/m'},{id:'min_yearly',label:'Min/y'},{id:'max_yearly',label:'Max/y'},{id:'min_abs',label:'Min∞'},{id:'max_abs',label:'Max∞'}] },
  consumption_counter:{ label: 'Verbrauch',      color: '#7c3aed', inputs: [{id:'value',label:'Zähler'}],                                              outputs: [{id:'daily',label:'Tag'},{id:'weekly',label:'Woche'},{id:'monthly',label:'Monat'},{id:'yearly',label:'Jahr'},{id:'prev_daily',label:'Vortag'},{id:'prev_weekly',label:'Vorwoche'},{id:'prev_monthly',label:'Vormonat'},{id:'prev_yearly',label:'Vorjahr'}] },
  // Timer (extended)
  operating_hours:    { label: 'Betriebsstd.',   color: '#b45309', inputs: [{id:'active',label:'Aktiv'},{id:'reset',label:'Reset'}],                   outputs: [{id:'hours',label:'Std.'}]       },
  // Notification
  notify_pushover:    { label: 'Pushover',       color: '#e11d48', inputs: [{id:'trigger',label:'Trigger'},{id:'message',label:'Nachricht'},{id:'url',label:'URL'},{id:'url_title',label:'URL-Titel'},{id:'image_url',label:'Bild-URL'}], outputs: [{id:'sent',label:'Gesendet'}] },
  notify_sms:         { label: 'SMS (seven.io)', color: '#e11d48', inputs: [{id:'trigger',label:'Trigger'},{id:'message',label:'Nachricht'}],           outputs: [{id:'sent',label:'Gesendet'}]    },
  // Math — avg_multi (dynamic inputs, fixed outputs)
  avg_multi: { label: 'Mittelwert', color: '#7c3aed',
    inputs: [{id:'in_1',label:'IN 1'},{id:'in_2',label:'IN 2'}],
    outputs: [
      {id:'avg',     label:'∅ aktuell'},
      {id:'avg_1m',  label:'∅ 1 min'},
      {id:'avg_1h',  label:'∅ 1 h'},
      {id:'avg_1d',  label:'∅ 1 Tag'},
      {id:'avg_7d',  label:'∅ 7 Tage'},
      {id:'avg_14d', label:'∅ 14 Tage'},
      {id:'avg_30d', label:'∅ 30 Tage'},
      {id:'avg_180d',label:'∅ 180 Tage'},
      {id:'avg_365d',label:'∅ 365 Tage'},
    ]
  },
  // String
  string_concat:      { label: 'String Verketten', color: '#0891b2', inputs: [{id:'in_1',label:'1'},{id:'in_2',label:'2'}], outputs: [{id:'result',label:'Ergebnis'}] },
  // Integration
  api_client:         { label: 'API Client',     color: '#0e7490', inputs: [{id:'trigger',label:'Trigger'},{id:'body',label:'Body'}],                  outputs: [{id:'response',label:'Antwort'},{id:'status',label:'Status'},{id:'success',label:'OK'}] },
  json_extractor:     { label: 'JSON Extraktor',     color: '#0369a1', inputs: [{id:'data',label:'Daten'}], outputs: [{id:'value',label:'Wert'}] },
  xml_extractor:      { label: 'XML Extraktor',      color: '#0369a1', inputs: [{id:'data',label:'Daten'}], outputs: [{id:'value',label:'Wert'}] },
  substring_extractor:{ label: 'Substring / RegEx',  color: '#0369a1', inputs: [{id:'data',label:'Daten'}], outputs: [{id:'value',label:'Wert'}] },
}

// ── Gate helpers ───────────────────────────────────────────────────────────
const isGateNode = computed(() =>
  props.type === 'and' || props.type === 'or' || props.type === 'xor'
)

// ── Computed def — expands gate + string_concat inputs dynamically
const def = computed(() => {
  const base = NODE_DEFS[props.type] ?? { label: props.type, color: '#475569', inputs: [], outputs: [] }
  if (isGateNode.value) {
    const count = Math.max(2, Math.min(30, Number(props.data?.input_count) || 2))
    const inputs = Array.from({ length: count }, (_, i) => ({
      id:    `in${i + 1}`,
      label: `IN ${i + 1}`,
    }))
    return { ...base, inputs, outputs: [{ id: 'out', label: 'Out' }] }
  }
  if (props.type === 'string_concat') {
    const count = Math.max(2, Math.min(20, Number(props.data?.count) || 2))
    const inputs = Array.from({ length: count }, (_, i) => ({
      id:    `in_${i + 1}`,
      label: String(i + 1),
    }))
    return { ...base, inputs, outputs: [{ id: 'result', label: 'Ergebnis' }] }
  }
  if (props.type === 'avg_multi') {
    const count = Math.max(2, Math.min(20, Number(props.data?.input_count) || 2))
    const inputs = Array.from({ length: count }, (_, i) => ({
      id:    `in_${i + 1}`,
      label: `IN ${i + 1}`,
    }))
    return { ...base, inputs }
  }
  return base
})

// ── Inline negation toggle (AND / OR / XOR) ────────────────────────────────
function toggleNegate(portId) {
  const key = `negate_${portId}`
  updateNodeData(props.id, { [key]: !props.data[key] })
}

// ── Config summary ─────────────────────────────────────────────────────────
const summary = computed(() => {
  const d = props.data
  if (props.type === 'const_value')  return `${d.data_type ?? 'number'} = ${d.value ?? '0'}`
  if (props.type === 'compare')      return `A ${d.operator ?? '>'} B`
  if (props.type === 'hysteresis')   return `ON≥${d.threshold_on ?? 25}  OFF≤${d.threshold_off ?? 20}`
  if (props.type === 'math_formula') return d.formula || 'a + b'
  if (props.type === 'math_map')     return `[${d.in_min ?? 0}‒${d.in_max ?? 100}] → [${d.out_min ?? 0}‒${d.out_max ?? 1}]`
  if (props.type === 'timer_delay')  return `${d.delay_s ?? 1} s`
  if (props.type === 'timer_pulse')  return `${d.duration_s ?? 1} s`
  if (props.type === 'timer_cron')   return d.cron || '0 7 * * *'
  if (props.type === 'mcp_tool')     return d.tool_name || '—'
  if (props.type === 'astro_sun')       return `${d.latitude ?? 47.37}° N  ${d.longitude ?? 8.54}° E`
  if (props.type === 'clamp')           return `[${d.min ?? 0} … ${d.max ?? 100}]`
  if (props.type === 'random_value')    return `${d.data_type ?? 'int'}  [${d.min ?? 0} … ${d.max ?? 100}]`
  if (props.type === 'statistics')      return null
  if (props.type === 'operating_hours') return null
  if (props.type === 'notify_pushover')     return d.title || 'open bridge server'
  if (props.type === 'notify_sms')          return d.to || '—'
  if (props.type === 'avg_multi') {
    const count = Math.max(2, Math.min(20, Number(d.input_count) || 2))
    return `${count} Eingänge`
  }
  if (props.type === 'string_concat') {
    const count = Math.max(2, Math.min(20, Number(d.count) || 2))
    const sep = d.separator != null && d.separator !== '' ? `"${String(d.separator).slice(0, 6)}"` : null
    return sep ? `${count} Teile · ${sep}` : `${count} Teile`
  }
  if (props.type === 'api_client')          return `${d.method ?? 'GET'}  ${(d.url || '—').slice(0, 20)}`
  if (props.type === 'json_extractor')      return d.json_path || '—'
  if (props.type === 'xml_extractor')       return d.xml_path  || '—'
  if (props.type === 'substring_extractor') {
    const modeLabel = { links_von:'links von', rechts_von:'rechts von', zwischen:'zwischen', ausschneiden:'ausschneiden', regex:'regex' }
    const m = modeLabel[d.mode] ?? d.mode ?? '—'
    const hint = d.mode === 'regex' ? (d.pattern || '—') : d.mode === 'zwischen' ? `"${d.start_marker ?? ''}…${d.end_marker ?? ''}"` : d.mode === 'ausschneiden' ? `[${d.start ?? 0}:${d.length ?? -1}]` : (d.search || '—')
    return `${m}  ${hint}`
  }
  if (props.type === 'heating_circuit')     return `W≤${d.temp_winter ?? 15} °C  S≥${d.temp_summer ?? 20} °C`
  if (props.type === 'min_max_tracker')     return null
  if (props.type === 'consumption_counter') return null
  if (props.type === 'gate') {
    const behavior = d.closed_behavior === 'default_value' ? `→ ${d.default_value ?? 0}` : '→ halten'
    return d.negate_enable ? `¬Freigabe  ${behavior}` : behavior
  }
  if (props.type === 'and' || props.type === 'or' || props.type === 'xor') {
    const count = Math.max(2, Math.min(30, Number(props.data?.input_count) || 2))
    return count > 2 ? `${count} Eingänge` : null
  }
  return null
})

// ── Layout constants ───────────────────────────────────────────────────────
const HEADER_H = 28   // px  header height
const SUMMARY_H = 20  // px  summary line height (only when present)
const PORT_H   = 22   // px  per port row height
const DEBUG_H  = 18   // px  debug value strip height (only when present)

const rowCount  = computed(() => Math.max(def.value.inputs.length, def.value.outputs.length, 1))
const summaryPx = computed(() => summary.value ? SUMMARY_H : 0)
const debugPx   = computed(() => props.data._dbg   ? DEBUG_H  : 0)
const cardH     = computed(() => HEADER_H + summaryPx.value + rowCount.value * PORT_H + debugPx.value + 8)

// port row indices (0..rowCount-1)
const portRows  = computed(() => Array.from({ length: rowCount.value }, (_, i) => i))

// ── Handle positioning — aligned with port rows ────────────────────────────
function hStyle(index, _total) {
  const bodyStart = HEADER_H + summaryPx.value
  const posY      = bodyStart + index * PORT_H + PORT_H / 2
  const topPct    = (posY / cardH.value * 100).toFixed(1)
  const root = document.documentElement
  const s    = getComputedStyle(root)
  return {
    top:     topPct + '%',
    zIndex:  '100',
    width:   '12px',
    height:  '12px',
    background:   s.getPropertyValue('--handle-in-bg').trim()  || '#94a3b8',
    border:  '2px solid ' + (s.getPropertyValue('--handle-border').trim() || '#0f172a'),
    borderRadius: '50%',
    cursor:  'crosshair',
  }
}

// ── Delete ─────────────────────────────────────────────────────────────────
const { removeNodes } = useVueFlow()
const hovered = ref(false)
function remove() { removeNodes([props.id]) }
</script>

<style scoped>
.gn-root  { position: relative; }

.gn-card  {
  min-width: 130px;
  border: 1px solid var(--node-card-border);
  border-top: 3px solid #475569;
  border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0,0,0,.3);
  background: var(--node-card-bg);
  overflow: visible;
}

.gn-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  border-radius: 5px 5px 0 0;
}
.gn-title { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:var(--node-title-color); }
.gn-del   { font-size:11px; color:var(--node-del-color); background:none; border:none; cursor:pointer; padding:0 2px; line-height:1; transition:color .15s; }
.gn-del:hover { color:#f87171; }

.gn-body  { padding: 0; }

.gn-summary {
  font-size: 10px;
  color: var(--node-summary-color);
  padding: 2px 10px;
  font-family: ui-monospace, monospace;
  border-bottom: 1px solid var(--node-card-border);
}

.gn-ports-rows { padding: 0 10px; }

.gn-port-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.gn-port-left  { font-size: 9px; color: var(--node-port-label); }
.gn-port-right { font-size: 9px; color: var(--node-port-label); }

/* Inline negation toggle */
.gn-port-negate {
  font-size: 9px;
  color: var(--node-port-label);
  background: none;
  border: none;
  padding: 0 2px;
  cursor: pointer;
  border-radius: 3px;
  line-height: 1;
  transition: background .12s, color .12s;
}
.gn-port-negate:hover          { background: rgba(255,255,255,.10); color: #7dd3fc; }
.gn-port-negate--active        { color: #f87171; font-weight: 700; }
.gn-port-negate--right         { margin-left: auto; }

.gn-debug {
  font-size: 9px;
  color: var(--node-debug-color);
  font-family: ui-monospace, monospace;
  padding: 2px 10px 4px;
  border-top: 1px solid var(--node-card-border);
  background: var(--node-debug-bg);
  border-radius: 0 0 6px 6px;
  letter-spacing: .02em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Handles */
.gn-root :deep(.vue-flow__handle) {
  background: var(--handle-in-bg) !important;
  border-color: var(--handle-border) !important;
}
.gn-root :deep(.vue-flow__handle.gn-out) {
  background: var(--handle-out-bg) !important;
}
.gn-root :deep(.vue-flow__handle:hover) {
  background: #38bdf8 !important;
  box-shadow: 0 0 0 4px rgba(56, 189, 248, 0.35) !important;
}
</style>
