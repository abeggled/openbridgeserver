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
        <span class="gn-title">{{ def.label }}</span>
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
              :title="$t('logic.negatePort', { id: def.inputs[r].id })"
              @click.stop="toggleNegate(def.inputs[r].id)"
            >{{ data[`negate_${def.inputs[r].id}`] ? `¬${def.inputs[r].label}` : def.inputs[r].label }}</button>
            <span v-else class="gn-port-left">{{ def.inputs[r]?.label }}</span>

            <!-- Output label — clickable negation toggle for gate nodes (only on last row) -->
            <button
              v-if="isGateNode && def.outputs[r]"
              class="gn-port-negate gn-port-negate--right nodrag"
              :class="{ 'gn-port-negate--active': !!data.negate_out }"
              :title="$t('logic.negateOutput')"
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
import { useI18n } from 'vue-i18n'

const { updateNodeData } = useVueFlow()
const { t, te } = useI18n()

const props = defineProps({
  id:   { type: String, required: true },
  type: { type: String, required: true },
  data: { type: Object, default: () => ({}) },
})

// ── Node definitions ───────────────────────────────────────────────────────
const NODE_DEFS = computed(() => ({
  const_value:  { label: 'Festwert',    color: '#475569', inputs: [],                                                                                                  outputs: [{id:'value',      label:t('logic.ports.value')}]       },
  and:          { label: 'AND',         color: '#1d4ed8', inputs: [{id:'in1',label:t('logic.ports.in_n',{n:1})},{id:'in2',label:t('logic.ports.in_n',{n:2})}],         outputs: [{id:'out',        label:t('logic.ports.out')}]         },
  or:           { label: 'OR',          color: '#1d4ed8', inputs: [{id:'in1',label:t('logic.ports.in_n',{n:1})},{id:'in2',label:t('logic.ports.in_n',{n:2})}],         outputs: [{id:'out',        label:t('logic.ports.out')}]         },
  not:          { label: 'NOT',         color: '#1d4ed8', inputs: [{id:'in1',label:t('logic.ports.in_n',{n:1})}],                                                      outputs: [{id:'out',        label:t('logic.ports.out')}]         },
  xor:          { label: 'XOR',         color: '#1d4ed8', inputs: [{id:'in1',label:t('logic.ports.in_n',{n:1})},{id:'in2',label:t('logic.ports.in_n',{n:2})}],         outputs: [{id:'out',        label:t('logic.ports.out')}]         },
  gate:         { label: 'TOR',         color: '#1d4ed8', inputs: [{id:'in',label:t('logic.ports.input')},{id:'enable',label:t('logic.ports.enable')}],                 outputs: [{id:'out',        label:t('logic.ports.output')}]      },
  compare:      { label: 'Vergleich',   color: '#1d4ed8', inputs: [{id:'in1',label:t('logic.ports.in_n',{n:1})},{id:'in2',label:t('logic.ports.in_n',{n:2})}],         outputs: [{id:'out',        label:t('logic.portLabels.resultShort')}] },
  hysteresis:   { label: 'Hysterese',   color: '#1d4ed8', inputs: [{id:'value',label:t('logic.ports.value')}],                                                         outputs: [{id:'out',        label:t('logic.ports.out')}]         },
  math_formula: { label: 'Formel',      color: '#7c3aed', inputs: [{id:'in1',label:t('logic.ports.in_n',{n:1})},{id:'in2',label:t('logic.ports.in_n',{n:2})}],          outputs: [{id:'result',     label:t('logic.portLabels.resultShort')}] },
  math_map:     { label: 'Skalieren',   color: '#7c3aed', inputs: [{id:'value',label:t('logic.ports.value')}],                                                         outputs: [{id:'result',     label:t('logic.portLabels.resultShort')}] },
  timer_delay:  { label: 'Verzögerung', color: '#b45309', inputs: [{id:'trigger',label:t('logic.ports.trigger')}],                                                     outputs: [{id:'trigger',    label:t('logic.ports.trigger')}]     },
  timer_pulse:  { label: 'Impuls',      color: '#b45309', inputs: [{id:'trigger',label:t('logic.ports.trigger')}],                                                     outputs: [{id:'out',        label:t('logic.ports.out')}]         },
  timer_cron:   { label: 'Trigger',     color: '#b45309', inputs: [],                                                                                                  outputs: [{id:'trigger',    label:t('logic.ports.trigger')}]     },
  mcp_tool:     { label: 'MCP Tool',    color: '#0e7490', inputs: [{id:'trigger',label:t('logic.ports.trigger')},{id:'input',label:t('logic.ports.input')}],            outputs: [{id:'result',     label:t('logic.portLabels.resultShort')},{id:'done',label:t('logic.ports.done')}] },
  python_script:{ label: 'Python',      color: '#be185d', inputs: [{id:'in1',label:t('logic.ports.in_n',{n:1})},{id:'in2',label:t('logic.ports.in_n',{n:2})},{id:'in3',label:t('logic.ports.in_n',{n:3})}], outputs: [{id:'result',label:t('logic.portLabels.resultShort')}] },
  // Astro
  astro_sun:          { label: 'Astro Sonne',    color: '#d97706', inputs: [],                                                                                          outputs: [{id:'sunrise',    label:t('logic.portLabels.sunrise')},{id:'sunset',label:t('logic.portLabels.sunset')},{id:'is_day',label:t('logic.portLabels.isDay')}] },
  // Math (extended)
  clamp:              { label: 'Begrenzer',      color: '#7c3aed', inputs: [{id:'value',label:t('logic.ports.value')}],                                                 outputs: [{id:'result',     label:t('logic.portLabels.resultShort')}] },
  random_value:       { label: 'Zufallswert',    color: '#7c3aed', inputs: [{id:'trigger',label:t('logic.ports.trigger')}],                                            outputs: [{id:'value',      label:t('logic.ports.value')}]       },
  statistics:         { label: 'Statistik',      color: '#7c3aed', inputs: [{id:'value',label:t('logic.ports.value')},{id:'reset',label:t('logic.ports.reset')}],       outputs: [{id:'min',label:t('logic.ports.min')},{id:'max',label:t('logic.ports.max')},{id:'avg',label:t('logic.ports.avg')},{id:'count',label:t('logic.ports.count')}] },
  heating_circuit:    { label: 'Sommer/Winter',  color: '#7c3aed', inputs: [{id:'value',label:t('logic.portLabels.tempC')}],                                            outputs: [{id:'heating_mode',label:t('logic.portLabels.heizmode')},{id:'daily_avg',label:t('logic.portLabels.dailyAvg')},{id:'monthly_avg',label:t('logic.portLabels.monthlyAvg')},{id:'t1',label:t('logic.portLabels.tDebug',{n:1})},{id:'t2',label:t('logic.portLabels.tDebug',{n:2})},{id:'t3',label:t('logic.portLabels.tDebug',{n:3})}] },
  min_max_tracker:    { label: 'Min/Max',        color: '#7c3aed', inputs: [{id:'value',label:t('logic.ports.value')}],                                                 outputs: [{id:'min_daily',label:t('logic.portLabels.minDaily')},{id:'max_daily',label:t('logic.portLabels.maxDaily')},{id:'min_weekly',label:t('logic.portLabels.minWeekly')},{id:'max_weekly',label:t('logic.portLabels.maxWeekly')},{id:'min_monthly',label:t('logic.portLabels.minMonthly')},{id:'max_monthly',label:t('logic.portLabels.maxMonthly')},{id:'min_yearly',label:t('logic.portLabels.minYearly')},{id:'max_yearly',label:t('logic.portLabels.maxYearly')},{id:'min_abs',label:t('logic.portLabels.minAbs')},{id:'max_abs',label:t('logic.portLabels.maxAbs')}] },
  consumption_counter:{ label: 'Verbrauch',      color: '#7c3aed', inputs: [{id:'value',label:t('logic.portLabels.counter')}],                                          outputs: [{id:'daily',label:t('logic.portLabels.daily')},{id:'weekly',label:t('logic.portLabels.weekly')},{id:'monthly',label:t('logic.portLabels.monthly')},{id:'yearly',label:t('logic.portLabels.yearly')},{id:'prev_daily',label:t('logic.portLabels.prevDaily')},{id:'prev_weekly',label:t('logic.portLabels.prevWeekly')},{id:'prev_monthly',label:t('logic.portLabels.prevMonthly')},{id:'prev_yearly',label:t('logic.portLabels.prevYearly')}] },
  // Timer (extended)
  operating_hours:    { label: 'Betriebsstd.',   color: '#b45309', inputs: [{id:'active',label:t('logic.ports.active')},{id:'reset',label:t('logic.ports.reset')}],     outputs: [{id:'hours',      label:t('logic.ports.hours')}]       },
  // Notification
  notify_pushover:    { label: 'Pushover',       color: '#e11d48', inputs: [{id:'trigger',label:t('logic.ports.trigger')},{id:'message',label:t('logic.ports.message')},{id:'url',label:'URL'},{id:'url_title',label:t('logic.portLabels.urlTitle')},{id:'image_url',label:t('logic.portLabels.imageUrl')}], outputs: [{id:'sent',label:t('logic.ports.sent')}] },
  notify_sms:         { label: 'SMS (seven.io)', color: '#e11d48', inputs: [{id:'trigger',label:t('logic.ports.trigger')},{id:'message',label:t('logic.ports.message')}], outputs: [{id:'sent',     label:t('logic.ports.sent')}]        },
  // Math — avg_multi (dynamic inputs, fixed outputs)
  avg_multi: { label: 'Mittelwert', color: '#7c3aed',
    inputs: [{id:'in_1',label:t('logic.ports.in_n',{n:1})},{id:'in_2',label:t('logic.ports.in_n',{n:2})}],
    outputs: [
      {id:'avg',      label:t('logic.portLabels.avgNow')},
      {id:'avg_1m',   label:t('logic.portLabels.avg1m')},
      {id:'avg_1h',   label:t('logic.portLabels.avg1h')},
      {id:'avg_1d',   label:t('logic.portLabels.avg1d')},
      {id:'avg_7d',   label:t('logic.portLabels.avg7d')},
      {id:'avg_14d',  label:t('logic.portLabels.avg14d')},
      {id:'avg_30d',  label:t('logic.portLabels.avg30d')},
      {id:'avg_180d', label:t('logic.portLabels.avg180d')},
      {id:'avg_365d', label:t('logic.portLabels.avg365d')},
    ]
  },
  // String
  string_concat:      { label: 'String Verketten', color: '#0891b2', inputs: [{id:'in_1',label:'1'},{id:'in_2',label:'2'}], outputs: [{id:'result',label:t('logic.ports.result')}] },
  // Integration
  api_client:         { label: 'API Client',     color: '#0e7490', inputs: [{id:'trigger',label:t('logic.ports.trigger')},{id:'body',label:t('logic.ports.body')}],     outputs: [{id:'response',   label:t('logic.ports.response')},{id:'status',label:t('logic.ports.status')},{id:'success',label:t('logic.ports.success')}] },
  json_extractor:     { label: 'JSON Extraktor',     color: '#0369a1', inputs: [{id:'data',label:t('logic.ports.data')}], outputs: [{id:'value',label:t('logic.ports.value')}] },
  xml_extractor:      { label: 'XML Extraktor',      color: '#0369a1', inputs: [{id:'data',label:t('logic.ports.data')}], outputs: [{id:'value',label:t('logic.ports.value')}] },
  substring_extractor:{ label: 'Substring / RegEx',  color: '#0369a1', inputs: [{id:'data',label:t('logic.ports.data')}], outputs: [{id:'value',label:t('logic.ports.value')}] },
  ical:               { label: 'iCalendar',          color: '#0369a1', inputs: [], outputs: [{id:'raw', label:t('logic.ports.raw')}] },
}))

// ── Gate helpers ───────────────────────────────────────────────────────────
const isGateNode = computed(() =>
  props.type === 'and' || props.type === 'or' || props.type === 'xor'
)

// ── Computed def — expands gate + string_concat inputs dynamically
const def = computed(() => {
  const base = NODE_DEFS.value[props.type] ?? { label: props.type, color: '#475569', inputs: [], outputs: [] }
  const label = te(`logic.nodeTypes.${props.type}`) ? t(`logic.nodeTypes.${props.type}`) : base.label
  if (isGateNode.value) {
    const count = Math.max(2, Math.min(30, Number(props.data?.input_count) || 2))
    const inputs = Array.from({ length: count }, (_, i) => ({
      id:    `in${i + 1}`,
      label: t('logic.ports.in_n', { n: i + 1 }),
    }))
    return { ...base, label, inputs, outputs: [{ id: 'out', label: t('logic.ports.out') }] }
  }
  if (props.type === 'string_concat') {
    const count = Math.max(2, Math.min(20, Number(props.data?.count) || 2))
    const inputs = Array.from({ length: count }, (_, i) => ({
      id:    `in_${i + 1}`,
      label: String(i + 1),
    }))
    return { ...base, label, inputs, outputs: [{ id: 'result', label: t('logic.ports.result') }] }
  }
  if (props.type === 'avg_multi') {
    const count = Math.max(2, Math.min(20, Number(props.data?.input_count) || 2))
    const inputs = Array.from({ length: count }, (_, i) => ({
      id:    `in_${i + 1}`,
      label: t('logic.ports.in_n', { n: i + 1 }),
    }))
    return { ...base, label, inputs }
  }
  if (props.type === 'ical') {
    const filterCount = Math.max(0, Math.min(20, Number(props.data?.filter_count) || 0))
    let filters = []
    try { filters = JSON.parse(props.data?.filters || '[]') } catch (_) { filters = [] }
    const outputs = [{ id: 'raw', label: t('logic.ports.raw') }]
    for (let i = 0; i < filterCount; i++) {
      const fname = (Array.isArray(filters) && filters[i]?.name) ? filters[i].name : `F${i + 1}`
      const lArr  = `${fname}: ${t('logic.portLabels.icalArray')}`
      const lDate = `${fname}: ${t('logic.portLabels.icalDate')}`
      const lTom  = `${fname}: ${t('logic.portLabels.icalTomorrow')}`
      const lTod  = `${fname}: ${t('logic.portLabels.icalToday')}`
      outputs.push(
        { id: `f${i}_array`,     label: lArr  },
        { id: `f${i}_next_date`, label: lDate },
        { id: `f${i}_tomorrow`,  label: lTom  },
        { id: `f${i}_today`,     label: lTod  },
      )
    }
    return { ...base, label, outputs }
  }
  if (props.type === 'json_extractor') {
    let pathList = []
    try { pathList = JSON.parse(props.data?.json_paths || '[]') } catch (_) { pathList = [] }
    if (Array.isArray(pathList) && pathList.length > 0) {
      const outputs = pathList.map((entry, i) => ({
        id:    `out_${i + 1}`,
        label: (entry?.label || `Wert ${i + 1}`),
      }))
      return { ...base, outputs }
    }
    return base
  }
  if (props.type === 'xml_extractor') {
    let pathList = []
    try { pathList = JSON.parse(props.data?.xml_paths || '[]') } catch (_) { pathList = [] }
    if (Array.isArray(pathList) && pathList.length > 0) {
      const outputs = pathList.map((entry, i) => ({
        id:    `out_${i + 1}`,
        label: (entry?.label || `Wert ${i + 1}`),
      }))
      return { ...base, outputs }
    }
    return base
  }
  return { ...base, label }
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
    return t('logic.summary.inputs', { n: count })
  }
  if (props.type === 'string_concat') {
    const count = Math.max(2, Math.min(20, Number(d.count) || 2))
    const sep = d.separator != null && d.separator !== '' ? `"${String(d.separator).slice(0, 6)}"` : null
    return sep ? `${t('logic.summary.parts', { n: count })} · ${sep}` : t('logic.summary.parts', { n: count })
  }
  if (props.type === 'ical') {
    const count = Number(d.filter_count) || 0
    const url = (d.url || '—').replace(/^https?:\/\//, '').slice(0, 22)
    return `${url} · ${t('logic.summary.filters', { n: count })}`
  }
  if (props.type === 'api_client')          return `${d.method ?? 'GET'}  ${(d.url || '—').slice(0, 20)}`
  if (props.type === 'json_extractor') {
    let pathList = []
    try { pathList = JSON.parse(d.json_paths || '[]') } catch (_) { pathList = [] }
    if (Array.isArray(pathList) && pathList.length > 0) return `${pathList.length} Ausgänge`
    return d.json_path || '—'
  }
  if (props.type === 'xml_extractor') {
    let pathList = []
    try { pathList = JSON.parse(d.xml_paths || '[]') } catch (_) { pathList = [] }
    if (Array.isArray(pathList) && pathList.length > 0) return `${pathList.length} Ausgänge`
    return d.xml_path || '—'
  }
  if (props.type === 'substring_extractor') {
    const MODES = ['links_von', 'rechts_von', 'zwischen', 'ausschneiden', 'regex']
    const m = MODES.includes(d.mode) ? t(`logic.summary.modes.${d.mode}`) : (d.mode ?? '—')
    const hint = d.mode === 'regex' ? (d.pattern || '—') : d.mode === 'zwischen' ? `"${d.start_marker ?? ''}…${d.end_marker ?? ''}"` : d.mode === 'ausschneiden' ? `[${d.start ?? 0}:${d.length ?? -1}]` : (d.search || '—')
    return `${m}  ${hint}`
  }
  if (props.type === 'heating_circuit') {
    const thr = d.threshold_temp ?? d.temp_winter ?? 14
    const hys = d.hysteresis ?? (d.temp_winter != null && d.temp_summer != null ? d.temp_summer - d.temp_winter : 2)
    return `Grenze ${thr} °C  +${hys} °C`
  }
  if (props.type === 'min_max_tracker')     return null
  if (props.type === 'consumption_counter') return null
  if (props.type === 'gate') {
    const behavior = d.closed_behavior === 'default_value' ? `→ ${d.default_value ?? 0}` : t('logic.summary.hold')
    return d.negate_enable ? `${t('logic.summary.negateEnable')}  ${behavior}` : behavior
  }
  if (props.type === 'and' || props.type === 'or' || props.type === 'xor') {
    const count = Math.max(2, Math.min(30, Number(props.data?.input_count) || 2))
    return count > 2 ? t('logic.summary.inputs', { n: count }) : null
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
