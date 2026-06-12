import { describe, expect, it } from 'vitest'
import {
  hierarchyDisplayIndent,
  hierarchyDisplayLabel,
  hierarchyDisplayPath,
  normalizeHierarchyDisplayDepth,
} from '@/utils/hierarchyDisplay'

describe('hierarchyDisplayPath', () => {
  it('prepends the tree name when display depth is disabled', () => {
    expect(
      hierarchyDisplayPath({
        treeName: 'Haus',
        path: ['EG', 'Kueche'],
        displayDepth: 0,
      }),
    ).toEqual(['Haus', 'EG', 'Kueche'])
  })

  it('starts at the configured display depth for deep nodes', () => {
    expect(
      hierarchyDisplayPath({
        treeName: 'Haus',
        path: ['Gebaeude', 'EG', 'Kueche'],
        displayDepth: 2,
        treeHasDisplayDepth: true,
      }),
    ).toEqual(['EG', 'Kueche'])
  })

  it('keeps tree context for shallow existing nodes above the display depth', () => {
    expect(
      hierarchyDisplayPath({
        treeName: 'Haus',
        path: ['Gebaeude'],
        displayDepth: 2,
        treeHasDisplayDepth: true,
      }),
    ).toEqual(['Haus', 'Gebaeude'])
  })

  it('can hide shallow nodes when building suggestions for a display-depth tree', () => {
    expect(
      hierarchyDisplayPath({
        treeName: 'Haus',
        path: ['Gebaeude'],
        displayDepth: 2,
        treeHasDisplayDepth: true,
        hideShallow: true,
      }),
    ).toEqual([])
  })

  it('normalizes invalid display depth values', () => {
    expect(normalizeHierarchyDisplayDepth('2.8')).toBe(2)
    expect(normalizeHierarchyDisplayDepth(-1)).toBe(0)
    expect(normalizeHierarchyDisplayDepth('nope')).toBe(0)
  })

  it('computes the visual indent relative to the visible start level', () => {
    expect(hierarchyDisplayIndent(['Gebaeude', 'EG', 'Kueche'], 2)).toBe(1)
    expect(hierarchyDisplayIndent(['Gebaeude'], 2)).toBe(0)
  })

  it('joins display labels with hierarchy separators', () => {
    expect(
      hierarchyDisplayLabel({
        treeName: 'Haus',
        path: ['EG', 'Kueche'],
        displayDepth: 0,
      }),
    ).toBe('Haus › EG › Kueche')
  })
})
