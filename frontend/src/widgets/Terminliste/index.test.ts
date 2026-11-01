import { describe, expect, it } from 'vitest'
import { WidgetRegistry } from '@/widgets/registry'
import './index'

describe('Terminliste registration', () => {
  const def = WidgetRegistry.get('Terminliste')

  it('registers a datapoint-free display widget', () => {
    expect(def).toBeDefined()
    expect(def?.label).toBe('widgets.terminliste.title')
    expect(def?.noDatapoint).toBe(true)
    expect(def?.defaultConfig).toMatchObject({ sources: [], days: 7, separator: 'semicolon', show_time: true })
  })

  it('subscribes the datapoints of all configured sources', () => {
    const ids = def?.getExtraDatapointIds?.({
      sources: [{ datapoint_id: 'dp-1' }, { datapoint_id: '' }, {}, null, { datapoint_id: 'dp-2' }],
    })
    expect(ids).toEqual(['dp-1', 'dp-2'])
    expect(def?.getExtraDatapointIds?.({})).toEqual([])
  })
})
