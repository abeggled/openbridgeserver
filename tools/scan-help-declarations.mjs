#!/usr/bin/env node
// Reads the help-relevant declarations out of the frontends and prints them as
// JSON for tools/check_help_contract.py.
//
// Every value here comes from a real parse — @babel/parser for JavaScript and
// TypeScript, @vue/compiler-sfc for single-file components. An earlier version
// of the gate scanned these files with regexes and a hand-written tokenizer,
// and review after review found the next language construct it misread:
// quoted property keys, comments after a closing quote, regex literals holding
// a quote, `${...}` expressions, spreads, shorthand properties, CSS custom
// properties shaped like a JS property. None of those are special cases for a
// parser — it simply sees the syntax tree the runtime sees.
//
// Output shape:
//   {
//     "routes":     [{ "name", "helpId"|null, "file", "line" }],
//     "widgets":    [{ "type", "file", "line" }],
//     "references": [{ "helpId", "file", "line" }],
//     "unreadable": [{ "kind", "file", "line", "problem" }]
//   }
//
// `unreadable` is how the gate fails closed: a declaration whose value is not
// a literal is reported, never skipped, because the surface ships either way
// and only the checker is left guessing.

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

// fileURLToPath, not `.pathname`: a checkout path containing a space stays
// percent-encoded in the URL and the resolved path would not exist.
const REPO_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)))
// `--root` lets the tests point the scan at a fixture tree; everything else
// resolves relative to it exactly as it does for the repository itself.
const rootArgument = process.argv.indexOf('--root')
const SCAN_ROOT = rootArgument < 0 ? REPO_ROOT : resolve(process.argv[rootArgument + 1])
// Resolved from the repository's gui/, which declares both parsers as
// devDependencies — a fixture tree has no node_modules of its own.
const requireFromGui = createRequire(join(REPO_ROOT, 'gui', 'package.json'))
const { parse: parseJs } = requireFromGui('@babel/parser')
const { parse: parseSfc } = requireFromGui('@vue/compiler-sfc')

const SOURCE_SUFFIXES = ['.vue', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs']
const REFERENCE_DIRS = ['gui/src', 'frontend/src']

const routes = []
const widgets = []
const references = []
const unreadable = []

const rel = (file) => relative(SCAN_ROOT, file).split('\\').join('/')

function babelOptions(file) {
  const plugins = ['typescript']
  if (file.endsWith('x')) plugins.push('jsx')
  return { sourceType: 'module', errorRecovery: true, plugins }
}

function parseSource(code, file) {
  try {
    return parseJs(code, babelOptions(file))
  } catch (error) {
    unreadable.push({ kind: 'parse', file: rel(file), line: error.loc?.line ?? 1, problem: `cannot be parsed: ${error.message}` })
    return null
  }
}

/** Walk every node of a Babel AST, depth first. */
function walk(node, visit, parent = null) {
  if (node === null || typeof node !== 'object') return
  if (Array.isArray(node)) {
    for (const child of node) walk(child, visit, parent)
    return
  }
  if (typeof node.type !== 'string') return
  visit(node, parent)
  for (const key of Object.keys(node)) {
    if (key === 'loc' || key === 'leadingComments' || key === 'trailingComments') continue
    walk(node[key], visit, node)
  }
}

/** The property named `name`, directly on an object expression.
 *
 * A computed key is accepted when it is a string literal — `{ ['name']: … }`
 * is the same property to Vue Router as `{ name: … }`, and skipping it would
 * hand the surface a silent exemption.
 */
function ownProperty(objectExpression, name) {
  return objectExpression.properties.find((property) => propertyKey(property) === name)
}

/** The static key of a property, or null when it cannot be resolved. */
function propertyKey(property) {
  if (property.type !== 'ObjectProperty') return null
  const key = unwrap(property.key)
  if (property.computed) return key.type === 'StringLiteral' ? key.value : null
  if (key.type === 'Identifier') return key.name
  return key.type === 'StringLiteral' ? key.value : null
}

/** A computed key whose value only the runtime knows. */
const hasDynamicKey = (objectExpression) =>
  objectExpression.properties.some((property) => property.type === 'ObjectProperty' && property.computed && propertyKey(property) === null)

// `x as T`, `x satisfies T`, `x!` and `(x)` wrap the expression without
// changing it; a widget definition written `{ … } satisfies WidgetDefinition`
// is still that object.
const TRANSPARENT_WRAPPERS = new Set(['TSAsExpression', 'TSSatisfiesExpression', 'TSNonNullExpression', 'TSTypeAssertion', 'ParenthesizedExpression', 'TypeCastExpression'])

function unwrap(node) {
  let current = node
  while (current && TRANSPARENT_WRAPPERS.has(current.type)) current = current.expression
  return current
}

const stringValue = (node) => {
  const inner = unwrap(node)
  return inner && inner.type === 'StringLiteral' ? inner.value : null
}
const hasSpread = (objectExpression) => objectExpression.properties.some((p) => p.type === 'SpreadElement')

// ── Admin routes ────────────────────────────────────────────────────────────

function collectRoutes(file) {
  const code = readFileSync(file, 'utf-8')
  const ast = parseSource(code, file)
  if (ast === null) return

  // Top level only: a `const routes = []` inside some helper function is not
  // the router table, and a depth-first walk would let it replace the real one.
  let declaration = null
  for (const statement of ast.program.body) {
    const declarations = statement.type === 'VariableDeclaration' ? statement.declarations : []
    for (const candidate of declarations) {
      if (candidate.id.type === 'Identifier' && candidate.id.name === 'routes') declaration = candidate
    }
  }
  const initialiser = unwrap(declaration?.init)
  if (declaration === null || !initialiser || initialiser.type !== 'ArrayExpression') {
    unreadable.push({ kind: 'route', file: rel(file), line: declaration?.loc?.start.line ?? 1, problem: "has no inline `routes` array the gate can read" })
    return
  }
  collectRouteRecords(initialiser, file)
}

function collectRouteRecords(arrayExpression, file) {
  for (const element of arrayExpression.elements) {
    if (element === null) continue
    const record = unwrap(element)
    if (record.type !== 'ObjectExpression') {
      // A spread contributes routes the parse cannot see.
      unreadable.push({ kind: 'route', file: rel(file), line: element.loc.start.line, problem: 'contributes routes the gate cannot read; list them inline' })
      continue
    }
    if (hasSpread(record)) {
      unreadable.push({ kind: 'route', file: rel(file), line: element.loc.start.line, problem: 'spreads into a route record, so what the gate reads is not what ships' })
      continue
    }
    if (hasDynamicKey(record)) {
      unreadable.push({ kind: 'route', file: rel(file), line: record.loc.start.line, problem: 'has a computed property key the gate cannot resolve' })
      continue
    }
    const children = ownProperty(record, 'children')
    if (children) {
      if (unwrap(children.value).type !== 'ArrayExpression') {
        unreadable.push({ kind: 'route', file: rel(file), line: children.loc.start.line, problem: "declares `children` the gate cannot read; give it an inline array" })
      } else {
        collectRouteRecords(unwrap(children.value), file)
      }
    }
    const nameProperty = ownProperty(record, 'name')
    const shorthand = record.properties.find((p) => p.type === 'ObjectProperty' && p.shorthand && p.key.type === 'Identifier' && p.key.name === 'name')
    if (!nameProperty) continue
    const name = stringValue(nameProperty.value)
    if (name === null) {
      unreadable.push({
        kind: 'route',
        file: rel(file),
        line: (shorthand ?? nameProperty).loc.start.line,
        problem: "declares a route whose `name` is not a string literal; the gate cannot tell which route this is",
      })
      continue
    }
    const meta = ownProperty(record, 'meta')
    const metaValue = meta ? unwrap(meta.value) : null
    let helpId = null
    if (metaValue && metaValue.type === 'ObjectExpression') {
      // Order decides: `{ ...base, helpId: 'a' }` is the ordinary way to
      // extend defaults and the literal wins, while anything unreadable *after*
      // the literal — a spread, a computed key — can replace it, and a meta
      // with no literal at all may be supplying one from somewhere unseen.
      const helpIdIndex = metaValue.properties.findIndex((property) => propertyKey(property) === 'helpId')
      const overrides = metaValue.properties.some(
        (property, index) =>
          index > helpIdIndex &&
          (property.type === 'SpreadElement' || (property.type === 'ObjectProperty' && property.computed && propertyKey(property) === null))
      )
      if (overrides) {
        unreadable.push({
          kind: 'route',
          file: rel(file),
          line: metaValue.loc.start.line,
          problem:
            helpIdIndex < 0
              ? 'has a `meta` that may supply a helpId the gate cannot see'
              : 'has a `meta` whose helpId can be overridden after it, so it is not necessarily what ships',
        })
        continue
      }
      const helpIdProperty = ownProperty(metaValue, 'helpId')
      if (helpIdProperty) {
        helpId = stringValue(helpIdProperty.value)
        if (helpId === null) {
          unreadable.push({ kind: 'route', file: rel(file), line: helpIdProperty.loc.start.line, problem: "declares a `meta.helpId` that is not a string literal" })
          continue
        }
      }
    }
    routes.push({ name, helpId, file: rel(file), line: record.loc.start.line })
  }
}

// ── Visu widget types ───────────────────────────────────────────────────────

function collectWidgets(file) {
  const code = readFileSync(file, 'utf-8')
  const ast = parseSource(code, file)
  if (ast === null) return

  walk(ast, (node) => {
    if (node.type !== 'CallExpression') return
    const callee = node.callee
    const isRegister =
      callee.type === 'MemberExpression' &&
      !callee.computed &&
      callee.property.type === 'Identifier' &&
      callee.property.name === 'register' &&
      callee.object.type === 'Identifier' &&
      callee.object.name === 'WidgetRegistry'
    if (!isRegister) return

    const definition = unwrap(node.arguments[0])
    const type =
      definition && definition.type === 'ObjectExpression' && !hasDynamicKey(definition) ? stringValue(ownProperty(definition, 'type')?.value) : null
    if (type === null) {
      unreadable.push({
        kind: 'widget',
        file: rel(file),
        line: node.loc.start.line,
        problem: "registers a widget whose `type` is not a string literal; the gate cannot tell which widget this is",
      })
      return
    }
    widgets.push({ type, file: rel(file), line: node.loc.start.line })
  })
}

// ── help_id references ──────────────────────────────────────────────────────

/** `helpId: 'x'` written anywhere in executable code. */
function collectScriptReferences(code, file, lineOffset = 0) {
  const ast = parseSource(code, file)
  if (ast === null) return
  walk(ast, (node) => {
    if (node.type !== 'ObjectProperty') return
    const key = propertyKey(node)
    const value = stringValue(node.value)
    if (key === null && node.computed && value !== null) {
      // The key may well be `helpId` at runtime, and then this is a live
      // button whose target the gate never checked.
      unreadable.push({
        kind: 'reference',
        file: rel(file),
        line: node.loc.start.line + lineOffset,
        problem: 'assigns a string through a computed key the gate cannot resolve; it may be a helpId',
      })
      return
    }
    if (key !== 'helpId' || value === null) return
    references.push({ helpId: value, file: rel(file), line: node.loc.start.line + lineOffset })
  })
}

/** `help-id="x"` on a component in an SFC template. */
function collectTemplateReferences(templateAst, file, lineOffset) {
  const visit = (node) => {
    if (node.props) {
      for (const prop of node.props) {
        // `type: 6` is a plain attribute; a `v-bind`/`:` binding is directive
        // type 7 and its value is only known at runtime.
        if (prop.type === 6 && prop.name === 'help-id' && prop.value) {
          references.push({ helpId: prop.value.content, file: rel(file), line: prop.loc.start.line + lineOffset })
        }
      }
    }
    for (const child of node.children ?? []) if (typeof child === 'object') visit(child)
  }
  visit(templateAst)
}

function collectReferences(file) {
  const code = readFileSync(file, 'utf-8')
  if (!file.endsWith('.vue')) {
    collectScriptReferences(code, file)
    return
  }
  let descriptor
  try {
    ;({ descriptor } = parseSfc(code, { filename: file }))
  } catch (error) {
    unreadable.push({ kind: 'parse', file: rel(file), line: 1, problem: `cannot be parsed as a single-file component: ${error.message}` })
    return
  }
  // Only <template> and <script> — a <style> block's CSS custom property may
  // be named like a JS property but declares nothing.
  if (descriptor.template?.ast) collectTemplateReferences(descriptor.template.ast, file, 0)
  for (const block of [descriptor.script, descriptor.scriptSetup]) {
    if (block) collectScriptReferences(block.content, file, block.loc.start.line - 1)
  }
}

// ── Entry point ─────────────────────────────────────────────────────────────

function* sourceFiles(dir) {
  let entries
  try {
    entries = readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name === 'node_modules') continue
    const full = join(dir, entry.name)
    if (entry.isDirectory()) yield* sourceFiles(full)
    else if (SOURCE_SUFFIXES.some((suffix) => entry.name.endsWith(suffix))) yield full
  }
}

const routerFile = join(SCAN_ROOT, 'gui', 'src', 'router', 'index.js')
if (statSync(routerFile, { throwIfNoEntry: false })) collectRoutes(routerFile)

const widgetsDir = join(SCAN_ROOT, 'frontend', 'src', 'widgets')
if (statSync(widgetsDir, { throwIfNoEntry: false })) {
  for (const entry of readdirSync(widgetsDir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const indexFile = join(widgetsDir, entry.name, 'index.ts')
    if (entry.isDirectory() && statSync(indexFile, { throwIfNoEntry: false })) collectWidgets(indexFile)
  }
}

for (const dir of REFERENCE_DIRS) for (const file of sourceFiles(join(SCAN_ROOT, dir))) collectReferences(file)

process.stdout.write(JSON.stringify({ routes, widgets, references, unreadable }) + '\n')
