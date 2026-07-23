import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormOnewire from '@/components/datapoints/binding-form/BindingFormOnewire.vue'

function mk(overrides = {}) {
  return mount(BindingFormOnewire, {
    props: {
      cfg: { sensor_id: '', property: '' },
      selectedInstanceId: 1,
      onewireSensors: [],
      onewireBrowseLoading: false,
      onewireBrowseError: null,
      ...overrides,
    },
  })
}

describe('BindingFormOnewire — sensor_id / property fields', () => {
  it('renders sensor_id and property inputs', () => {
    const inputs = mk().findAll('input')
    expect(inputs.length).toBeGreaterThanOrEqual(2)
  })

  it('first input is bound to cfg.sensor_id', () => {
    const w = mk({ cfg: { sensor_id: '28.4B057F0A1C10', property: '' } })
    expect(w.findAll('input')[0].element.value).toBe('28.4B057F0A1C10')
  })

  it('second input is bound to cfg.property', () => {
    const w = mk({ cfg: { sensor_id: '', property: 'humidity' } })
    expect(w.findAll('input')[1].element.value).toBe('humidity')
  })

  it('updating sensor_id input mutates cfg', async () => {
    const cfg = { sensor_id: '', property: '' }
    const w = mk({ cfg })
    await w.findAll('input')[0].setValue('28.AABBCCDDEEFF')
    expect(cfg.sensor_id).toBe('28.AABBCCDDEEFF')
  })

  it('updating property input mutates cfg', async () => {
    const cfg = { sensor_id: '', property: '' }
    const w = mk({ cfg })
    await w.findAll('input')[1].setValue('PIO.0')
    expect(cfg.property).toBe('PIO.0')
  })

  it('first input has required attribute', () => {
    expect(mk().findAll('input')[0].attributes('required')).toBeDefined()
  })
})

describe('BindingFormOnewire — scan button', () => {
  it('disabled when no selectedInstanceId', () => {
    const btn = mk({ selectedInstanceId: null }).findAll('button').find(b => b.text().includes('Scannen'))
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('emits onewire-browse on click', async () => {
    const w = mk()
    const btn = w.findAll('button').find(b => b.text().includes('Scannen'))
    await btn.trigger('click')
    expect(w.emitted('onewire-browse')).toBeTruthy()
  })

  it('shows browse error when onewireBrowseError is set', () => {
    const w = mk({ onewireBrowseError: 'owserver nicht erreichbar' })
    expect(w.text()).toContain('owserver nicht erreichbar')
  })
})

describe('BindingFormOnewire — scan results', () => {
  const sensor = { rom_id: '28.4B057F0A1C10', family: '28', properties: ['temperature', 'humidity'], alias: 'Gästebad' }

  it('shows scanned sensors', () => {
    const w = mk({ onewireSensors: [sensor] })
    expect(w.text()).toContain('28.4B057F0A1C10')
    expect(w.text()).toContain('temperature')
    expect(w.text()).toContain('humidity')
  })

  it('emits select-onewire-sensor with rom_id and property when a property badge is clicked', async () => {
    const w = mk({ onewireSensors: [sensor] })
    const badge = w.findAll('button').find(b => b.text() === 'humidity')
    await badge.trigger('click')
    expect(w.emitted('select-onewire-sensor')[0][0]).toEqual({ rom_id: '28.4B057F0A1C10', property: 'humidity' })
  })

  it('alias input reflects the sensor alias', () => {
    const w = mk({ onewireSensors: [sensor] })
    const aliasInput = w.findAll('input').find(i => i.element.value === 'Gästebad')
    expect(aliasInput).toBeTruthy()
  })

  it('emits update-onewire-alias-draft when the alias input changes', async () => {
    const w = mk({ onewireSensors: [sensor] })
    const aliasInput = w.findAll('input').find(i => i.element.value === 'Gästebad')
    await aliasInput.setValue('Keller Nord')
    expect(w.emitted('update-onewire-alias-draft')[0][0]).toEqual({ romId: '28.4B057F0A1C10', label: 'Keller Nord' })
  })

  it('emits save-onewire-alias with the rom_id when the save button is clicked', async () => {
    const w = mk({ onewireSensors: [sensor] })
    const saveBtn = w.findAll('button').find(b => b.text().includes('Speichern'))
    await saveBtn.trigger('click')
    expect(w.emitted('save-onewire-alias')[0][0]).toBe('28.4B057F0A1C10')
  })

  it('renders multiple sensors', () => {
    const other = { rom_id: '29.1122334455AA', family: '29', properties: ['PIO.0'], alias: null }
    const w = mk({ onewireSensors: [sensor, other] })
    expect(w.text()).toContain('29.1122334455AA')
    expect(w.text()).toContain('PIO.0')
  })
})
