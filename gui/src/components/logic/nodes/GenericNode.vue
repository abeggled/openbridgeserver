<template>
  <div class="gn-wrap" @mouseenter="hovered = true" @mouseleave="hovered = false">

    <!-- Input handles (LEFT) — rendered first but must be above card via z-index -->
    <Handle
      v-for="(inp, i) in def.inputs" :key="'in-' + inp.id"
      type="target"
      :id="inp.id"
      :position="Position.Left"
      :style="{ top: hPos(i, def.inputs.length) }"
    />

    <!-- Node body -->
    <div class="gn-card" :style="{ borderTopColor: def.color }">
      <div class="gn-header" :style="{ backgroundColor: def.color + '28' }">
        <span class="gn-label">{{ data.label || def.label }}</span>
        <button v-show="hovered" class="gn-delete nodrag" @click.stop="remove" title="Block löschen">✕</button>
      </div>
      <div v-if="summary" class="gn-summary">{{ summary }}</div>
      <div v-if="def.inputs.length || def.outputs.length" class="gn-ports">
        <div class="gn-port-col">
          <span v-for="inp in def.inputs" :key="inp.id" class="gn-port-label">{{ inp.label }}</span>
        </div>
        <div class="gn-port-col" style="align-items:flex-end">
          <span v-for="out in def.outputs" :key="out.id" class="gn-port-label">{{ out.label }}</span>
        </div>
      </div>
    </div>

    <!-- Output handles (RIGHT) -->
    <Handle
      v-for="(out, i) in def.outputs" :key="'out-' + out.id"
      type="source"
      :id="out.id"
      :position="Position.Right"
      :style="{ top: hPos(i, def.outputs.length) }"
      class="gn-handle-out"
    />

  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Handle, Position, useVueFlow } from '@vue-flow/core'

const props = defineProps({
  id:   { type: String, required: true },
  type: { type: String, required: true },
  data: { type: Object, default: () => ({}) },
})

const NODE_DEFS = {
  and:          { label: 'AND',         color: '#1d4ed8', inputs: [{id:'a',label:'A'},{id:'b',label:'B'}],                              outputs: [{id:'out',   label:'Out'}]      },
  or:           { label: 'OR',          color: '#1d4ed8', inputs: [{id:'a',label:'A'},{id:'b',label:'B'}],                              outputs: [{id:'out',   label:'Out'}]      },
  not:          { label: 'NOT',         color: '#1d4ed8', inputs: [{id:'in',label:'In'}],                                               outputs: [{id:'out',   label:'Out'}]      },
  xor:          { label: 'XOR',         color: '#1d4ed8', inputs: [{id:'a',label:'A'},{id:'b',label:'B'}],                              outputs: [{id:'out',   label:'Out'}]      },
  compare:      { label: 'Vergleich',   color: '#1d4ed8', inputs: [{id:'a',label:'A'},{id:'b',label:'B'}],                              outputs: [{id:'out',   label:'Erg.'}]     },
  hysteresis:   { label: 'Hysterese',   color: '#1d4ed8', inputs: [{id:'value',label:'Wert'}],                                          outputs: [{id:'out',   label:'Out'}]      },
  math_formula: { label: 'Formel',      color: '#7c3aed', inputs: [{id:'a',label:'a'},{id:'b',label:'b'},{id:'c',label:'c'}],           outputs: [{id:'result',label:'Erg.'}]     },
  math_map:     { label: 'Skalieren',   color: '#7c3aed', inputs: [{id:'value',label:'Wert'}],                                          outputs: [{id:'result',label:'Erg.'}]     },
  timer_delay:  { label: 'Verzögerung', color: '#b45309', inputs: [{id:'trigger',label:'Trigger'}],                                     outputs: [{id:'trigger',label:'Trigger'}] },
  timer_pulse:  { label: 'Impuls',      color: '#b45309', inputs: [{id:'trigger',label:'Trigger'}],                                     outputs: [{id:'out',   label:'Out'}]      },
  timer_cron:   { label: 'Zeitplan',    color: '#b45309', inputs: [],                                                                   outputs: [{id:'trigger',label:'Trigger'}] },
  mcp_tool:     { label: 'MCP Tool',    color: '#0e7490', inputs: [{id:'trigger',label:'Trigger'},{id:'input',label:'Input'}],           outputs: [{id:'result',label:'Erg.'},{id:'done',label:'Fertig'}] },
}

const def = computed(() => NODE_DEFS[props.type] ?? { label: props.type, color: '#475569', inputs: [], outputs: [] })

const summary = computed(() => {
  const d = props.data
  if (props.type === 'compare')      return `A ${d.operator ?? '>'} B`
  if (props.type === 'hysteresis')   return `ON≥${d.threshold_on ?? 25} OFF≤${d.threshold_off ?? 20}`
  if (props.type === 'math_formula') return d.formula ?? 'a + b'
  if (props.type === 'math_map')     return `[${d.in_min ?? 0}‒${d.in_max ?? 100}]→[${d.out_min ?? 0}‒${d.out_max ?? 1}]`
  if (props.type === 'timer_delay')  return `${d.delay_s ?? 1} s`
  if (props.type === 'timer_pulse')  return `${d.duration_s ?? 1} s`
  if (props.type === 'timer_cron')   return d.cron ?? '0 7 * * *'
  if (props.type === 'mcp_tool')     return d.tool_name || '—'
  return null
})

function hPos(index, total) {
  if (total === 1) return '50%'
  const step = 100 / (total + 1)
  return `${step * (index + 1)}%`
}

const { removeNodes } = useVueFlow()
const hovered = ref(false)
function remove() { removeNodes([props.id]) }
</script>

<style scoped>
.gn-wrap { position: relative; }

/* Force all Vue Flow handles inside this node above the card */
.gn-wrap :deep(.vue-flow__handle) {
  z-index: 20;
  width: 12px;
  height: 12px;
  background: #94a3b8;
  border: 2px solid #0f172a;
  border-radius: 50%;
  cursor: crosshair;
}
.gn-wrap :deep(.vue-flow__handle.gn-handle-out) {
  background: #60a5fa;
}
/* Hover state for connections */
.gn-wrap :deep(.vue-flow__handle:hover) {
  background: #38bdf8;
  transform: translate(-50%, -50%) scale(1.3);
}

.gn-card {
  min-width: 130px;
  background: #1e293b;
  border: 1px solid #334155;
  border-top: 3px solid #475569;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,.4);
  /* Ensure card does NOT capture pointer events over handle areas */
  position: relative;
  z-index: 1;
}

.gn-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 10px;
  border-radius: 5px 5px 0 0;
}

.gn-label {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: #f1f5f9;
}

.gn-delete {
  font-size: 11px;
  color: #64748b;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
  transition: color .15s;
}
.gn-delete:hover { color: #f87171; }

.gn-summary {
  font-size: 10px;
  color: #94a3b8;
  padding: 2px 10px 4px;
  font-family: ui-monospace, monospace;
  border-bottom: 1px solid #263347;
}

.gn-ports {
  display: flex;
  justify-content: space-between;
  padding: 3px 10px 5px;
}
.gn-port-col {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.gn-port-label {
  font-size: 9px;
  color: #64748b;
}
</style>
