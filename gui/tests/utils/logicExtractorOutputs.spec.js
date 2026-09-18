import { describe, it, expect } from 'vitest'
import { EXTRACTOR_MAX_PATHS, extractorOutputLabels, extractorRowCount, flattenJsonPaths, parseExtractorJson, retainPreviews } from '@/utils/logicExtractorOutputs'

const t = (key, params) => `${key}:${params?.n}`

describe('extractorOutputLabels', () => {
  it('maps out_N ports of a json_extractor to the configured row labels', () => {
    const node = {
      type: 'json_extractor',
      data: { json_paths: JSON.stringify([{ label: 'On/Off', path: 'Status.Power' }, { label: 'IP', path: 'StatusNET.IPAddress' }]) },
    }
    expect(extractorOutputLabels(node, t)).toEqual({ out_1: 'On/Off', out_2: 'IP' })
  })

  it('reads xml_paths for an xml_extractor', () => {
    const node = { type: 'xml_extractor', data: { xml_paths: JSON.stringify([{ label: 'Temp', path: './/temperature' }]) } }
    expect(extractorOutputLabels(node, t)).toEqual({ out_1: 'Temp' })
  })

  it('falls back to the translated "Wert N" for rows without a label when t is given', () => {
    const node = { type: 'json_extractor', data: { json_paths: JSON.stringify([{ label: '  ', path: 'a' }, { path: 'b' }]) } }
    expect(extractorOutputLabels(node, t)).toEqual({
      out_1: 'logic.nodeConfig.extractor.valueN:1',
      out_2: 'logic.nodeConfig.extractor.valueN:2',
    })
  })

  it('keeps the technical port id for unlabelled rows without t', () => {
    const node = { type: 'json_extractor', data: { json_paths: JSON.stringify([{ label: '', path: 'a' }, { label: 'B', path: 'b' }]) } }
    expect(extractorOutputLabels(node)).toEqual({ out_2: 'B' })
  })

  it('returns an empty map for other node types, missing nodes and unparsable rows', () => {
    expect(extractorOutputLabels({ type: 'const_value', data: { json_paths: '[{"label":"x"}]' } }, t)).toEqual({})
    expect(extractorOutputLabels(null, t)).toEqual({})
    expect(extractorOutputLabels({ type: 'json_extractor', data: { json_paths: '{not json' } }, t)).toEqual({})
    expect(extractorOutputLabels({ type: 'json_extractor', data: { json_paths: '{"label":"obj"}' } }, t)).toEqual({})
    expect(extractorOutputLabels({ type: 'json_extractor' }, t)).toEqual({})
  })
})

describe('retainPreviews', () => {
  const payload = '{"Status":{"Power":1}}'

  it('keeps the previous preview when the new run reports none for that node', () => {
    const previous = { j1: { out_1: 1, _preview: payload } }
    const outputs = { j1: { out_1: null, out_2: null, _preview: null } }
    expect(retainPreviews(previous, outputs)).toEqual({ j1: { out_1: null, out_2: null, _preview: payload } })
  })

  it('treats an undefined preview as missing but an empty string as a received document', () => {
    const previous = { j1: { _preview: payload }, x1: { _preview: '<a/>' } }
    const outputs = { j1: { out_1: 2 }, x1: { out_1: null, _preview: '' } }
    expect(retainPreviews(previous, outputs)).toEqual({
      j1: { out_1: 2, _preview: payload },
      x1: { out_1: null, _preview: '' },
    })
  })

  it('prefers the freshly received preview over the retained one', () => {
    const previous = { j1: { _preview: payload } }
    const outputs = { j1: { out_1: 5, _preview: '{"new":true}' } }
    expect(retainPreviews(previous, outputs)).toEqual({ j1: { out_1: 5, _preview: '{"new":true}' } })
  })

  it('adds a preview-only entry when the node is absent from the new run', () => {
    const previous = { j1: { _preview: payload } }
    expect(retainPreviews(previous, { n1: { value: 1 } })).toEqual({ n1: { value: 1 }, j1: { _preview: payload } })
    expect(retainPreviews(previous, { j1: 'broken' })).toEqual({ j1: { _preview: payload } })
  })

  it('ignores previous nodes without a preview and tolerates missing arguments', () => {
    const previous = { n1: { value: 1 }, n2: null, j1: { _preview: null } }
    expect(retainPreviews(previous, { n1: { value: 2 } })).toEqual({ n1: { value: 2 } })
    expect(retainPreviews(undefined, undefined)).toEqual({})
  })

  it('retains an empty received document like any other', () => {
    const previous = { x1: { _preview: '' } }
    expect(retainPreviews(previous, { x1: { out_1: null, _preview: null } })).toEqual({ x1: { out_1: null, _preview: '' } })
  })

  it('carries the pruned marker along with a retained preview and drops it otherwise', () => {
    const pruned = { j1: { _preview: payload, _preview_pruned: true } }
    expect(retainPreviews(pruned, { j1: { out_1: null, _preview: null } })).toEqual({ j1: { out_1: null, _preview: payload, _preview_pruned: true } })
    const full = { j1: { _preview: payload } }
    expect(retainPreviews(full, { j1: { out_1: null, _preview_pruned: true } })).toEqual({ j1: { out_1: null, _preview: payload } })
    // A fresh (unpruned) preview wins over the retained pruned one.
    expect(retainPreviews(pruned, { j1: { _preview: '{"a":1}' } })).toEqual({ j1: { _preview: '{"a":1}' } })
  })

  it('does not mutate its inputs', () => {
    const previous = { j1: { _preview: payload } }
    const outputs = { j1: { out_1: null } }
    retainPreviews(previous, outputs)
    expect(outputs).toEqual({ j1: { out_1: null } })
    expect(previous).toEqual({ j1: { _preview: payload } })
  })
})

describe('parseExtractorJson', () => {
  it('returns plain documents as-is', () => {
    expect(parseExtractorJson('{"a":1}')).toEqual({ a: 1 })
    expect(parseExtractorJson('[1,2]')).toEqual([1, 2])
    expect(parseExtractorJson('7')).toBe(7)
  })

  it('unwraps one level of double-encoded JSON objects and arrays', () => {
    const inner = { days: [{ SUNSET: '19:33' }] }
    expect(parseExtractorJson(JSON.stringify(JSON.stringify(inner)))).toEqual(inner)
    expect(parseExtractorJson(JSON.stringify('[1]'))).toEqual([1])
  })

  it('keeps strings whose content is not a JSON object', () => {
    expect(parseExtractorJson(JSON.stringify('42'))).toBe('42')
    expect(parseExtractorJson(JSON.stringify('null'))).toBe('null')
    expect(parseExtractorJson(JSON.stringify('{not json'))).toBe('{not json')
  })

  it('throws like JSON.parse for a snapshot that is not JSON', () => {
    expect(() => parseExtractorJson('{not json')).toThrow()
  })
})

describe('flattenJsonPaths', () => {
  it('lists every leaf of nested objects and arrays', () => {
    expect(flattenJsonPaths({ a: [{ b: 1 }, 2], c: { d: null }, e: 'x' })).toEqual(['a[0].b', 'a[1]', 'c.d', 'e'])
  })

  it('stops descending below depth 6 and lists the container itself', () => {
    const doc = { l1: { l2: { l3: { l4: { l5: { l6: { l7: { l8: 1 } } } } } } } }
    expect(flattenJsonPaths(doc)).toEqual(['l1.l2.l3.l4.l5.l6.l7'])
  })

  it('returns nothing for scalar or empty documents', () => {
    expect(flattenJsonPaths(42)).toEqual([])
    expect(flattenJsonPaths(null)).toEqual([])
    expect(flattenJsonPaths({})).toEqual([])
    expect(flattenJsonPaths([])).toEqual([])
  })

  it('bounds the list at the limit without throwing on large flat arrays', () => {
    const flat = Array.from({ length: EXTRACTOR_MAX_PATHS + 1000 }, (_, i) => i)
    const paths = flattenJsonPaths({ flat, tail: { x: 1 } })
    expect(paths).toHaveLength(EXTRACTOR_MAX_PATHS)
    expect(paths.at(-1)).toBe(`flat[${EXTRACTOR_MAX_PATHS - 1}]`)
  })

  it('honours a custom limit across nesting levels', () => {
    const doc = { a: [1, 2, 3], b: { c: [4, 5] }, d: 6 }
    expect(flattenJsonPaths(doc, 4)).toEqual(['a[0]', 'a[1]', 'a[2]', 'b.c[0]'])
    expect(flattenJsonPaths(doc, 0)).toEqual([])
  })
})

describe('extractorRowCount', () => {
  it('counts the configured rows of json and xml extractors', () => {
    expect(extractorRowCount({ type: 'json_extractor', data: { json_paths: JSON.stringify([{ path: 'a' }, { path: 'b' }]) } })).toBe(2)
    expect(extractorRowCount({ type: 'xml_extractor', data: { xml_paths: JSON.stringify([{ path: './/a' }]) } })).toBe(1)
    expect(extractorRowCount({ type: 'json_extractor', data: {} })).toBe(0)
  })

  it('is 0 for other node types, missing nodes and unparsable rows', () => {
    expect(extractorRowCount({ type: 'and', data: { json_paths: '[{}]' } })).toBe(0)
    expect(extractorRowCount(null)).toBe(0)
    expect(extractorRowCount({ type: 'json_extractor', data: { json_paths: '{oops' } })).toBe(0)
  })
})
