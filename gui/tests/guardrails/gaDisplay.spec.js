/**
 * Guardrail (#1296): the Admin GUI shows KNX group addresses only through
 * formatGa(address, knxProject.groupAddressStyle), never the raw API text.
 * Contract: docs/architecture/knx-group-addresses.md.
 *
 * It scans every SFC template under src/ for display positions (text
 * interpolation, v-text/v-html, and bound title/placeholder/aria-label/alt/
 * label/value) and fails when one of them renders a group address path as it
 * is. A "group address path" is, by the field names of the API:
 *   - a member chain ending in `address`, `group_address`,
 *     `state_group_address` or `ga_address` (`ga.address`, `dp.ga_address`);
 *   - the alias of a v-for over such a list (`ga in co.ga_addresses`).
 * It is checked per operand, so `a + ga.address`, `x || ga.address` and
 * `${ga.address}` in a template literal count; a function call such as
 * formatGa(ga.address, …) or knxGaLabel(ga.address) does not.
 *
 * Not seen: strings built in <script> and rendered through another name,
 * addresses passed down as a prop under another name, input v-model values.
 * Those stay with review and the component tests in all three styles.
 */
import { describe, it, expect } from 'vitest'
import { parse } from '@vue/compiler-sfc'

const SOURCES = import.meta.glob('../../src/**/*.vue', { query: '?raw', import: 'default', eager: true })

const DISPLAY_ATTRS = new Set(['title', 'placeholder', 'aria-label', 'alt', 'label', 'value'])
const GA_FIELDS = new Set(['address', 'group_address', 'state_group_address', 'ga_address'])
const GA_LISTS = new Set(['ga_addresses', 'group_addresses'])
const PATH = /^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\[\d+\])*$/

// Element 1, text 2, interpolation 5, attribute 6, directive 7
const ELEMENT = 1
const INTERPOLATION = 5
const DIRECTIVE = 7

function operands(expression) {
  const text = expression
    .replace(/`((?:\\.|[^`\\])*)`/g, (_m, body) => [...body.matchAll(/\$\{([^}]*)\}/g)].map(m => m[1]).join(' + ') || '""')
    .replace(/'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g, '""')
    .replace(/\?\./g, '.')
  return text.split(/\|\||\?\?|&&|[+?:,]/).map(part => part.trim())
}

function lastSegment(path) {
  return path.split('.').at(-1).replace(/\[\d+\]$/, '')
}

function rendersRawAddress(expression, aliases) {
  return operands(expression).some(operand => PATH.test(operand) && (GA_FIELDS.has(lastSegment(operand)) || aliases.has(operand)))
}

function forAlias(directive) {
  const match = /^\s*\(?\s*([A-Za-z_$][\w$]*)[^)]*?\)?\s+(?:in|of)\s+(.+)$/s.exec(directive.exp?.content ?? '')
  if (!match) return null
  const source = match[2].trim()
  return PATH.test(source) && GA_LISTS.has(lastSegment(source)) ? match[1] : null
}

/** `line: expression` for every display position that renders a raw group address. */
function rawGroupAddressDisplays(source) {
  const template = parse(source).descriptor.template
  if (!template) return []
  const found = []
  const check = (expression, loc, aliases) => {
    if (expression && rendersRawAddress(expression, aliases)) found.push(`${loc.start.line}: ${expression.trim()}`)
  }
  const walk = (node, aliases) => {
    if (node.type === INTERPOLATION) check(node.content.content, node.loc, aliases)
    if (node.type !== ELEMENT && node.type !== 0) return
    let scope = aliases
    for (const prop of node.props ?? []) {
      if (prop.type !== DIRECTIVE) continue
      if (prop.name === 'for') {
        const alias = forAlias(prop)
        if (alias) scope = new Set([...aliases, alias])
      }
    }
    for (const prop of node.props ?? []) {
      if (prop.type !== DIRECTIVE || !prop.exp) continue
      const display = prop.name === 'text' || prop.name === 'html' || (prop.name === 'bind' && DISPLAY_ATTRS.has(prop.arg?.content))
      if (display) check(prop.exp.content, prop.loc, scope)
    }
    for (const child of node.children ?? []) walk(child, scope)
  }
  walk(template.ast, new Set())
  return found
}

const sfc = template => `<template>${template}</template>\n<script setup>\n</script>\n`

describe('group address display guardrail', () => {
  it('finds no raw group address display in the Admin GUI', () => {
    const findings = Object.entries(SOURCES).flatMap(([file, source]) => rawGroupAddressDisplays(source).map(hit => `${file.replace('../../', '')}:${hit}`))
    expect(findings).toEqual([])
  })

  it.each([
    ['an interpolated address field', '<span>{{ ga.address }}</span>'],
    ['a ga_address field with optional chaining', '<span>{{ dp?.ga_address }}</span>'],
    ['the binding config field', '<span>{{ b.config.group_address }}</span>'],
    ['the feedback address in a fallback', `<span>{{ cfg.state_group_address || '-' }}</span>`],
    ['an address inside a template literal', '<span>{{ `${ga.address} (${ga.name})` }}</span>'],
    ['an address in a concatenation', `<span>{{ 'GA ' + item.address }}</span>`],
    ['a ternary branch', `<span>{{ ok ? ga.address : '' }}</span>`],
    ['a title attribute', '<span :title="dp.ga_address">x</span>'],
    ['v-text', '<span v-text="ga.address" />'],
    ['the alias of a v-for over ga_addresses', '<span v-for="ga in co.ga_addresses" :key="ga">{{ ga }}</span>'],
    ['the alias of a v-for with index', '<i v-for="(addr, i) in device.group_addresses" :key="i"><b>{{ addr }}</b></i>'],
  ])('flags %s', (_label, template) => {
    expect(rawGroupAddressDisplays(sfc(template))).toHaveLength(1)
  })

  it.each([
    ['formatGa', '<span>{{ formatGa(ga.address, knxProject.groupAddressStyle) }}</span>'],
    ['formatGa over a v-for alias', '<span v-for="ga in co.ga_addresses" :key="ga">{{ formatGa(ga, style) }}</span>'],
    ['a lookup by address', '<span>{{ knxGaLabel(ga.address) }}</span>'],
    ['other fields of a group address', '<span>{{ ga.name }} {{ ga.dpt }} {{ knxGaContext(ga.address).dpt }}</span>'],
    ['keys and test ids', '<li v-for="ga in list" :key="ga.address" :data-key="ga.address">x</li>'],
    ['physical addresses', '<span>{{ device.pa }} {{ gw.individual_address }} {{ backbone.multicast_address }}</span>'],
    ['translated text', `<span>{{ $t('adapters.bindingForm.groupAddressLabel') }}</span>`],
    ['a string literal naming the field', `<span>{{ 'group_address' }}</span>`],
    ['v-model', '<input v-model="cfg.group_address" />'],
    ['the alias outside its v-for', '<div><span v-for="ga in co.ga_addresses" :key="ga" /><span>{{ ga }}</span></div>'],
  ])('leaves %s alone', (_label, template) => {
    expect(rawGroupAddressDisplays(sfc(template))).toEqual([])
  })
})
