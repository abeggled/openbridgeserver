/**
 * Guardrail (#1296): the Admin GUI shows KNX group addresses only through
 * formatGa(address, knxProject.groupAddressStyle), never the raw API text.
 * Contract: docs/architecture/knx-group-addresses.md (Enforcement, layer 4).
 *
 * Rule: in a template, every read of a group address field fails, in whatever
 * form (member, bracket, method or function call, concatenation, template
 * literal), unless it sits inside the arguments of an allowed call:
 *   - formatGa(...)                 the formatter;
 *   - knxGaLabel(...), knxGaContext(...)  lookups by address that return a name
 *     or a context object, not the address.
 * Group address fields are the API's names: `address`, `group_address`,
 * `state_group_address`, `ga_address`, `ga_addresses`, `group_addresses`, plus
 * the alias of a v-for over a list of them (also destructured).
 *
 * Checked: text interpolation and every directive expression except those that
 * never display: v-on handlers, v-model, v-if/v-else-if/v-show, v-slot, the
 * v-for source (it feeds the alias tracking), and :key, :ref, :class, :style,
 * :data-*, and :value on <option>.
 *
 * Not seen (left to review and the component tests in all three styles):
 * strings built in <script> (computed properties, methods) and rendered under
 * another name, a v-for over a list returned by a function (`gaList(co)`),
 * dynamic property access (`dp[key]`), a formatGa argument that is not a
 * single address, formatGa with a hard-coded style instead of the project's,
 * and addresses destructured from slot props (`v-slot="{ address }"`). A non-address field that happens to be named `address`
 * in a checked position fails too; wrap it in a named helper or rename it.
 */
import { describe, it, expect } from 'vitest'
import { parse } from '@vue/compiler-sfc'

const SOURCES = import.meta.glob('../../src/**/*.vue', { query: '?raw', import: 'default', eager: true })

const GA_TOKENS = /(?<![\w$])(ga_address|group_address|state_group_address|ga_addresses|group_addresses)(?![\w$])|\.\s*address(?![\w$])/
const GA_LIST = /(?<![\w$])(ga_addresses|group_addresses)(?![\w$])/
const GA_FIELD_NAMES = ['address', 'group_address', 'state_group_address', 'ga_address']
const ALLOWED_CALLS = ['formatGa', 'knxGaLabel', 'knxGaContext']
const SKIPPED_DIRECTIVES = new Set(['on', 'model', 'if', 'else-if', 'show', 'slot', 'for'])
const SKIPPED_BINDS = new Set(['key', 'ref', 'class', 'style'])

// Node types of the template AST: root 0, element 1, interpolation 5, directive 7
const ROOT = 0
const ELEMENT = 1
const INTERPOLATION = 5
const DIRECTIVE = 7

/** The expression as code only: bracket keys as members, no string literals, template literals reduced to their ${} parts. */
function codeOnly(expression) {
  return expression
    .replace(/\[\s*(['"`])([\w$]+)\1\s*\]/g, '.$2')
    .replace(/`((?:\\.|[^`\\])*)`/g, (_m, body) => [...body.matchAll(/\$\{([^}]*)\}/g)].map(m => `(${m[1]})`).join(' + ') || '""')
    .replace(/'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"/g, '""')
}

/** Removes the argument lists of allowed calls (balanced parentheses). */
function withoutAllowedCalls(code) {
  const call = new RegExp(`(?<![\\w$.])(${ALLOWED_CALLS.join('|')})\\s*\\(`, 'g')
  let out = ''
  let last = 0
  for (let match = call.exec(code); match; match = call.exec(code)) {
    let depth = 1
    let i = match.index + match[0].length
    for (; i < code.length && depth; i++) depth += code[i] === '(' ? 1 : code[i] === ')' ? -1 : 0
    out += code.slice(last, match.index) + match[1] + '()'
    last = i
    call.lastIndex = i
  }
  return out + code.slice(last)
}

function readsGroupAddress(expression, aliases) {
  const code = withoutAllowedCalls(codeOnly(expression))
  if (GA_TOKENS.test(code)) return true
  return [...aliases].some(alias => new RegExp(`(?<![\\w$.])${alias.replace('$', '\\$')}(?![\\w$])`).test(code))
}

/** Names a v-for brings into scope that hold group addresses. */
function forAliases(directive) {
  const match = /^\s*(.+?)\s+(?:in|of)\s+(.+)$/s.exec(directive.exp?.content ?? '')
  if (!match) return []
  const [, left, source] = match
  const names = left.replace(/^\(|\)$/g, '')
  const destructured = /^\s*\{([^}]*)\}/.exec(names)
  if (destructured) {
    return destructured[1]
      .split(',')
      .map(part => part.split(':').map(name => name.trim()))
      .filter(([key]) => GA_FIELD_NAMES.includes(key))
      .map(([key, alias]) => alias || key)
  }
  const value = names.split(',')[0].trim()
  return GA_LIST.test(codeOnly(source)) ? [value] : []
}

function checked(node, prop) {
  if (SKIPPED_DIRECTIVES.has(prop.name)) return false
  if (prop.name !== 'bind') return true
  const arg = prop.arg?.content ?? ''
  if (SKIPPED_BINDS.has(arg) || arg.startsWith('data-')) return false
  return !(arg === 'value' && node.tag === 'option')
}

/** `line: expression` for every template read of a group address outside an allowed call. */
function rawGroupAddressReads(source) {
  const template = parse(source).descriptor.template
  if (!template) return []
  const found = []
  const check = (expression, loc, aliases) => {
    if (expression && readsGroupAddress(expression, aliases)) found.push(`${loc.start.line}: ${expression.trim()}`)
  }
  const walk = (node, aliases) => {
    if (node.type === INTERPOLATION) check(node.content.content, node.loc, aliases)
    if (node.type !== ELEMENT && node.type !== ROOT) return
    let scope = aliases
    for (const prop of node.props ?? []) {
      if (prop.type === DIRECTIVE && prop.name === 'for') scope = new Set([...aliases, ...forAliases(prop)])
    }
    for (const prop of node.props ?? []) {
      if (prop.type === DIRECTIVE && prop.exp && checked(node, prop)) check(prop.exp.content, prop.loc, scope)
    }
    for (const child of node.children ?? []) walk(child, scope)
  }
  walk(template.ast, new Set())
  return found
}

const sfc = template => `<template>${template}</template>\n<script setup>\n</script>\n`
const source = file => SOURCES[`../../src/${file}`]

describe('group address display guardrail', () => {
  it('finds no raw group address read in the Admin GUI', () => {
    const findings = Object.entries(SOURCES).flatMap(([file, text]) => rawGroupAddressReads(text).map(hit => `${file.replace('../../', '')}:${hit}`))
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
    ['a method on the address', '<span>{{ dp.ga_address.trim() }}</span>'],
    ['a joined address list', `<span>{{ co.ga_addresses.join(', ') }}</span>`],
    ['a function call that is no formatter', '<span>{{ String(dp.ga_address) }}</span>'],
    ['bracket access', `<span>{{ dp['ga_address'] }}</span>`],
    ['a formatter call that returns something else, then the raw address', '<span>{{ formatGa(a, s) + ga.address }}</span>'],
    ['a title attribute', '<span :title="dp.ga_address">x</span>'],
    ['a prop under another name', '<PathLabel :path="dp.ga_address" />'],
    ['v-text', '<span v-text="ga.address" />'],
    ['v-html', '<span v-html="dp.ga_address" />'],
    ['the alias of a v-for over ga_addresses', '<span v-for="ga in co.ga_addresses" :key="ga">{{ ga }}</span>'],
    ['the alias of a v-for with index', '<i v-for="(addr, i) in device.group_addresses" :key="i"><b>{{ addr }}</b></i>'],
    ['a destructured v-for alias', '<i v-for="{ address: a } in rows" :key="a">{{ a }}</i>'],
  ])('flags %s', (_label, template) => {
    expect(rawGroupAddressReads(sfc(template))).toHaveLength(1)
  })

  it.each([
    ['formatGa', '<span>{{ formatGa(ga.address, knxProject.groupAddressStyle) }}</span>'],
    ['formatGa over a v-for alias', '<span v-for="ga in co.ga_addresses" :key="ga">{{ formatGa(ga, style) }}</span>'],
    ['formatGa with a nested call in its arguments', '<span>{{ formatGa(String(dp.ga_address), style) }}</span>'],
    ['lookups by address', '<span>{{ knxGaLabel(ga.address) }} {{ knxGaContext(ga.address).dpt }}</span>'],
    ['other fields of a group address', '<span>{{ ga.name }} {{ ga.dpt }} {{ ga.addressBook }}</span>'],
    ['keys, refs, classes and data attributes', '<li v-for="ga in list" :key="ga.address" :ref="ga.address" :class="{ on: ga.address }" :data-key="ga.address">x</li>'],
    ['handlers, conditions and v-model', '<input v-if="ga.address" v-show="dp.ga_address" v-model="cfg.group_address" @click="select(ga.address)" />'],
    ['option values', '<select><option v-for="ga in co.ga_addresses" :key="ga" :value="ga">{{ formatGa(ga, s) }}</option></select>'],
    ['physical addresses', '<span>{{ device.pa }} {{ gw.individual_address }} {{ backbone.multicast_address }}</span>'],
    ['translated text', `<span>{{ $t('adapters.bindingForm.groupAddressLabel') }}</span>`],
    ['a string literal naming the field', `<span>{{ 'group_address' }} {{ "x.address" }}</span>`],
    ['the alias outside its v-for', '<div><span v-for="ga in co.ga_addresses" :key="ga" /><span>{{ ga }}</span></div>'],
  ])('leaves %s alone', (_label, template) => {
    expect(rawGroupAddressReads(sfc(template))).toEqual([])
  })

  // The critic's 14 mutations of the real components (P4b round 1), replayed on the current sources.
  const BOUND = '{{ formatGa(dp.ga_address, knxProject.groupAddressStyle) }}'
  const CHIP = '{{ formatGa(ga, knxProject.groupAddressStyle) }}'
  const MUTATIONS = [
    ['M1 raw v-for alias', 'views/KnxDevicesView.vue', CHIP, '{{ ga }}', true],
    ['M2 title', 'views/KnxDevicesView.vue', `data-testid="knx-device-bound-ga">${BOUND}`, `data-testid="knx-device-bound-ga" :title="dp.ga_address">${BOUND}`, true],
    ['M3 computed property', 'views/KnxDevicesView.vue', BOUND, '{{ boundGaText }}', false],
    ['M4 other alias', 'views/KnxDevicesView.vue', `v-for="ga in co.ga_addresses"
                  :key="ga"`, `v-for="addr in co.ga_addresses"
                  :key="addr" :title="addr"`, true],
    ['M5 list from a function', 'views/KnxDevicesView.vue', `v-for="ga in co.ga_addresses"`, `v-for="ga in gaList(co)"`, false],
    ['M6 prop under another name', 'views/KnxDevicesView.vue', BOUND, `<PathLabel :path="dp.ga_address" />`, true],
    ['M7 string built in script', 'views/KnxDevicesView.vue', BOUND, '{{ boundLabel(dp) }}', false],
    ['M8 trim', 'views/KnxDevicesView.vue', BOUND, '{{ dp.ga_address.trim() }}', true],
    ['M9 join', 'views/KnxDevicesView.vue', CHIP, `{{ co.ga_addresses.join(', ') }}`, true],
    ['M10 String()', 'views/KnxDevicesView.vue', BOUND, '{{ String(dp.ga_address) }}', true],
    ['M11 bracket access', 'views/KnxDevicesView.vue', BOUND, `{{ dp['ga_address'] }}`, true],
    ['M12 template literal title', 'components/ui/GaCombobox.vue', '<li v-for="(ga, i) in suggestions" :key="ga.address"', '<li v-for="(ga, i) in suggestions" :key="ga.address" :title="`GA ${ga.address}`"', true],
    ['M13 raw conflict column', 'views/SettingsView.vue', '{{ formatGa(conflict.address, knxProject.groupAddressStyle) }}', '{{ conflict.address }}', true],
    ['M14 v-html', 'views/KnxDevicesView.vue', `<span class="font-mono" data-testid="knx-device-bound-ga">${BOUND}</span>`, '<span class="font-mono" data-testid="knx-device-bound-ga" v-html="dp.ga_address" />', true],
  ]

  it.each(MUTATIONS)('%s', (_label, file, from, to, caught) => {
    const original = source(file)
    expect(original.split(from)).toHaveLength(2)
    expect(rawGroupAddressReads(original.replace(from, to)).length > 0).toBe(caught)
  })
})
