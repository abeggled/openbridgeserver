import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ZeitschaltuhrCustomHolidaysEditor from '@/components/adapters/ZeitschaltuhrCustomHolidaysEditor.vue'

function mk(modelValue = []) {
  return mount(ZeitschaltuhrCustomHolidaysEditor, {
    props: { modelValue },
  })
}

describe('ZeitschaltuhrCustomHolidaysEditor — rendering', () => {
  it('shows empty message when modelValue is empty', () => {
    expect(mk().text()).toContain('Keine benutzerdefinierten Feiertage')
  })

  it('shows existing entries', () => {
    const w = mk(['25-12:Weihnachten', 'easter+1:Ostermontag'])
    expect(w.text()).toContain('Weihnachten')
    expect(w.text()).toContain('Ostermontag')
  })

  it('formats fixed date entry as human-readable', () => {
    const w = mk(['01-01:Neujahr'])
    expect(w.text()).toContain('Neujahr')
    expect(w.text()).toContain('Januar')
  })

  it('formats easter-relative entry', () => {
    const w = mk(['easter-2:Karfreitag'])
    expect(w.text()).toContain('Karfreitag')
    expect(w.text()).toContain('Ostern')
  })

  it('renders type select with 5 options', () => {
    const options = mk().find('select').findAll('option')
    expect(options.length).toBe(5)
  })
})

describe('ZeitschaltuhrCustomHolidaysEditor — type-conditional fields', () => {
  it('shows month/day inputs for fixed type (default)', () => {
    const w = mk()
    const monthSelect = w.findAll('select').find(s => {
      const opts = s.findAll('option').map(o => o.element.value)
      return opts.includes('JAN') && opts.includes('DEZ')
    })
    expect(monthSelect).toBeTruthy()
    expect(w.find('input[type="number"]').exists()).toBe(true)
  })

  it('shows easter offset inputs for easter type', async () => {
    const w = mk()
    const typeSelect = w.find('select')
    await typeSelect.setValue('easter')
    await typeSelect.trigger('change')
    const signSelects = w.findAll('select').filter(s => {
      const opts = s.findAll('option').map(o => o.element.value)
      return opts.includes('+') && opts.includes('-')
    })
    expect(signSelects.length).toBeGreaterThan(0)
  })

  it('shows last_weekday selects for last_weekday type', async () => {
    const w = mk()
    const typeSelect = w.find('select')
    await typeSelect.setValue('last_weekday')
    await typeSelect.trigger('change')
    const weekdaySelect = w.findAll('select').find(s => {
      const opts = s.findAll('option').map(o => o.element.value)
      return opts.includes('MO') && opts.includes('SO')
    })
    expect(weekdaySelect).toBeTruthy()
  })

  it('shows n-th / weekday / month for nth_weekday type', async () => {
    const w = mk()
    const typeSelect = w.find('select')
    await typeSelect.setValue('nth_weekday')
    await typeSelect.trigger('change')
    const nSelect = w.findAll('select').find(s => {
      const opts = s.findAll('option').map(o => o.element.value)
      return opts.includes('1') && opts.includes('5') && opts.length === 5
    })
    expect(nSelect).toBeTruthy()
  })
})

describe('ZeitschaltuhrCustomHolidaysEditor — preview and add', () => {
  it('shows preview entry when name is filled (fixed date)', async () => {
    const w = mk()
    const nameInput = w.find('input[type="text"]')
    await nameInput.setValue('Mein Feiertag')
    expect(w.text()).toContain('Eintrag:')
    expect(w.text()).toContain('01-01:Mein Feiertag')
  })

  it('add button is disabled when name is empty', () => {
    expect(mk().find('button.btn-primary').attributes('disabled')).toBeDefined()
  })

  it('add button is enabled when name is filled', async () => {
    const w = mk()
    await w.find('input[type="text"]').setValue('Feiertag')
    expect(w.find('button.btn-primary').attributes('disabled')).toBeUndefined()
  })

  it('emits update:modelValue with new entry on add', async () => {
    const w = mk()
    await w.find('input[type="text"]').setValue('Silvester')
    await w.find('button.btn-primary').trigger('click')
    const emitted = w.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    // emitted[0][0] is an array of entries; the new entry contains 'Silvester'
    expect(emitted[0][0].some(e => e.includes('Silvester'))).toBe(true)
  })

  it('clears name after adding', async () => {
    const w = mk()
    await w.find('input[type="text"]').setValue('Silvester')
    await w.find('button.btn-primary').trigger('click')
    expect(w.find('input[type="text"]').element.value).toBe('')
  })

  it('emits correct entry string for easter type', async () => {
    const w = mk()
    await w.find('select').setValue('easter')
    const nameInput = w.find('input[type="text"]')
    await nameInput.setValue('Karfreitag')
    // Set offset to 2
    const offsetInput = w.find('input[type="number"]')
    await offsetInput.setValue(2)
    // Set sign to minus
    const signSelect = w.findAll('select').find(s => s.findAll('option').map(o => o.element.value).includes('-'))
    await signSelect.setValue('-')
    await w.find('button.btn-primary').trigger('click')
    const emitted = w.emitted('update:modelValue')
    expect(emitted[0][0]).toContain('easter-2:Karfreitag')
  })
})

describe('ZeitschaltuhrCustomHolidaysEditor — remove', () => {
  it('emits update:modelValue without the removed entry', async () => {
    const w = mk(['01-01:Neujahr', '25-12:Weihnachten'])
    const removeBtns = w.findAll('button[title="Entfernen"]')
    await removeBtns[0].trigger('click')
    const emitted = w.emitted('update:modelValue')
    // emitted[0][0] is the new array of remaining entries
    expect(emitted[0][0].some(e => e.includes('Neujahr'))).toBe(false)
    expect(emitted[0][0].some(e => e.includes('Weihnachten'))).toBe(true)
  })

  it('shows two remove buttons for two entries', () => {
    const w = mk(['01-01:Neujahr', '25-12:Weihnachten'])
    expect(w.findAll('button[title="Entfernen"]').length).toBe(2)
  })
})

describe('ZeitschaltuhrCustomHolidaysEditor — formatEntry', () => {
  it('formats last_weekday entry as human readable', () => {
    const w = mk(['last_MO_JAN:Letzter Montag'])
    expect(w.text()).toContain('Letzter Montag im Januar')
  })

  it('formats nth_weekday entry', () => {
    const w = mk(['2_DI_MRZ:2. Dienstag']) // MRZ fallback to MAR -> März
    expect(w.text()).toContain('Dienstag')
  })

  it('formats easter+0 as Ostersonntag', () => {
    const w = mk(['easter+0:Ostern'])
    expect(w.text()).toContain('Ostersonntag')
  })

  it('shows raw entry string when format is unknown', () => {
    const w = mk(['unknown_format'])
    expect(w.text()).toContain('unknown_format')
  })
})
