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

// Vue accepts a prop under either spelling, in templates and in render
// functions alike.
const HELP_PROP_NAMES = ['helpId', 'help-id']

// Vite's resolve order for an extensionless import.
const WIDGET_ENTRY_SUFFIXES = ['.mjs', '.js', '.mts', '.ts', '.jsx', '.tsx']

const SOURCE_SUFFIXES = ['.vue', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts']
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

/** Does this function body declare `name` itself, shadowing the outer one? */
function declaresBinding(fn, name) {
  const body = fn.body && fn.body.type === 'BlockStatement' ? fn.body.body : []
  const params = (fn.params ?? []).some((param) => param.type === 'Identifier' && param.name === name)
  return (
    params ||
    body.some(
      (statement) =>
        (statement.type === 'VariableDeclaration' && statement.declarations.some((d) => d.id.type === 'Identifier' && d.id.name === name)) ||
        (statement.type === 'FunctionDeclaration' && statement.id?.name === name)
    )
  )
}

const FUNCTION_TYPES = ['FunctionDeclaration', 'FunctionExpression', 'ArrowFunctionExpression', 'ObjectMethod', 'ClassMethod']

/** Walk the AST, skipping any function that shadows `name` with its own binding. */
function walkOutsideShadow(node, name, visit) {
  if (node === null || typeof node !== 'object') return
  if (Array.isArray(node)) {
    for (const child of node) walkOutsideShadow(child, name, visit)
    return
  }
  if (typeof node.type !== 'string') return
  if (FUNCTION_TYPES.includes(node.type) && declaresBinding(node, name)) return
  visit(node)
  for (const key of Object.keys(node)) {
    if (key === 'loc' || key === 'leadingComments' || key === 'trailingComments') continue
    walkOutsideShadow(node[key], name, visit)
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
  // `findLast`: a duplicated key is legal and the last one wins at runtime.
  return objectExpression.properties.findLast((property) => propertyKey(property) === name)
}

/** The static key of a property, or null when it cannot be resolved. */
function propertyKey(property, constants) {
  if (property.type !== 'ObjectProperty' && property.type !== 'ObjectMethod') return null
  const key = unwrap(property.key)
  const literal = staticKeyValue(key, constants)
  if (property.computed) return literal
  if (key.type === 'Identifier') return key.name
  return literal
}

/** Module-level `const NAME = 'literal'` bindings, for resolving computed keys.
 *
 * `obj[key] = 'x'` is how ordinary code writes into a map, so a computed key
 * the parse cannot resolve is skipped rather than failed on — refusing to
 * read `busy[a.id] = 'test'` would fail the gate over code that has nothing
 * to do with help. Resolving the constant instead catches the case that
 * matters — `const k = 'helpId'; props[k] = '…'` — without that cost.
 */
function stringConstants(ast) {
  const constants = new Map()
  for (const statement of ast.program.body) {
    const inner = statement.type === 'ExportNamedDeclaration' ? statement.declaration : statement
    if (!inner || inner.type !== 'VariableDeclaration' || inner.kind !== 'const') continue
    for (const declarator of inner.declarations) {
      const value = stringValue(declarator.init)
      if (declarator.id.type === 'Identifier' && value !== null) constants.set(declarator.id.name, value)
    }
  }
  return constants
}

/** A key written as a literal — `'name'` or `` `name` `` — or null. */
function staticKeyValue(key, constants) {
  if (key.type === 'StringLiteral') return key.value
  if (key.type === 'TemplateLiteral' && key.expressions.length === 0) return key.quasis[0].value.cooked
  if (key.type === 'Identifier' && constants) return constants.get(key.name) ?? null
  return null
}

/** A computed key whose value only the runtime knows. */
const hasDynamicKey = (objectExpression) =>
  objectExpression.properties.some(
    (property) => (property.type === 'ObjectProperty' || property.type === 'ObjectMethod') && property.computed && propertyKey(property) === null
  )

/** A getter/setter/method under one of `names` — a value only the runtime has.
 *
 * A computed key counts as well when it resolves to a literal: `get ['name']()`
 * is the same accessor.
 */
const hasAccessor = (objectExpression, names) =>
  objectExpression.properties.some((property) => property.type === 'ObjectMethod' && names.includes(propertyKey(property)))

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
  if (!inner) return null
  if (inner.type === 'StringLiteral') return inner.value
  // `name: `Dashboard`` is the same string; only an interpolation makes it
  // something the parse cannot know.
  if (inner.type === 'TemplateLiteral' && inner.expressions.length === 0) return inner.quasis[0].value.cooked
  return null
}
const hasSpread = (objectExpression) => objectExpression.properties.some((p) => p.type === 'SpreadElement')

// ── Admin routes ────────────────────────────────────────────────────────────

/** The identifier `createRouter({ routes })` is handed, if it names one. */
function routerTableName(ast) {
  let name = null
  let overriddenTable = null
  walk(ast, (node) => {
    if (node.type !== 'CallExpression') return
    const callee = unwrap(node.callee)
    const called = callee.type === 'Identifier' ? callee.name : callee.type === 'MemberExpression' && !callee.computed ? callee.property.name : null
    if (called !== 'createRouter') return
    const options = unwrap(node.arguments[0])
    if (!options || options.type !== 'ObjectExpression') return
    // A duplicated option resolves to the last one at runtime.
    const routesIndex = options.properties.findLastIndex((property) => propertyKey(property) === 'routes')
    // A spread after it can replace the table wholesale, and so can a computed
    // key the parse cannot read.
    const overridden = options.properties.some(
      (property, index) =>
        index > routesIndex &&
        (property.type === 'SpreadElement' || (property.type === 'ObjectProperty' && property.computed && propertyKey(property) === null))
    )
    if (overridden) {
      overriddenTable = node
      return
    }
    const routes = routesIndex < 0 ? null : options.properties[routesIndex]
    if (!routes) return
    // `{ routes }` shorthand, or `{ routes: someTable }`.
    const value = unwrap(routes.value)
    if (value && value.type === 'Identifier') name = value.name
  })
  return { name, overriddenTable }
}

function collectRoutes(file) {
  const code = readFileSync(file, 'utf-8')
  const ast = parseSource(code, file)
  if (ast === null) return

  // The table is the one `createRouter({ routes })` is given — following the
  // name alone would read a differently named array while the router runs on
  // something else entirely. Top level only, and `export const` counts:
  // a declaration inside a helper is not the router's.
  const { name: routerName, overriddenTable } = routerTableName(ast)
  if (overriddenTable !== null) {
    unreadable.push({
      kind: 'route',
      file: rel(file),
      line: overriddenTable.loc.start.line,
      problem: 'hands createRouter options whose `routes` can be replaced after it; the gate cannot tell which table ships',
    })
    return
  }
  const tableName = routerName ?? 'routes'
  let declaration = null
  for (const statement of ast.program.body) {
    const inner = statement.type === 'ExportNamedDeclaration' ? statement.declaration : statement
    const declarations = inner && inner.type === 'VariableDeclaration' ? inner.declarations : []
    for (const candidate of declarations) {
      if (candidate.id.type === 'Identifier' && candidate.id.name === tableName) declaration = candidate
    }
  }
  const initialiser = unwrap(declaration?.init)
  if (declaration === null || !initialiser || initialiser.type !== 'ArrayExpression') {
    unreadable.push({ kind: 'route', file: rel(file), line: declaration?.loc?.start.line ?? 1, problem: `has no inline \`${tableName}\` array the gate can read` })
    return
  }
  // Anything that changes the table after it is written contributes routes
  // this parse never sees. Only the module's own `routes` counts: a helper
  // with a local one of the same name is unrelated.
  walkOutsideShadow(ast, tableName, (node) => {
    if (node.type === 'AssignmentExpression') {
      const target = node.left
      const whole = target.type === 'Identifier' && target.name === tableName
      // `routes[0] = …` swaps out a record the scan already read.
      const element = target.type === 'MemberExpression' && unwrap(target.object).type === 'Identifier' && unwrap(target.object).name === tableName
      if (whole || element) {
        unreadable.push({
          kind: 'route',
          file: rel(file),
          line: node.loc.start.line,
          problem: whole ? 'reassigns the routes table; the gate cannot see what replaces it' : 'assigns into the routes table; the gate cannot see what replaces the record',
        })
        return
      }
    }
    if (node.type !== 'CallExpression') return
    const callee = node.callee
    if (callee.type !== 'MemberExpression') return
    const object = unwrap(callee.object)
    const method = callee.computed ? stringValue(callee.property) : callee.property.type === 'Identifier' ? callee.property.name : null
    if (object.type === 'Identifier' && object.name === tableName && ROUTE_MUTATORS.includes(method)) {
      unreadable.push({ kind: 'route', file: rel(file), line: node.loc.start.line, problem: `mutates the ${tableName} array with ${method}(); the gate cannot see what it adds` })
    }
  })

  collectRouteRecords(initialiser, file)
}

// `concat` returns a new array and leaves the table alone; flagging it
// would fail a push over code that changes nothing.
const ROUTE_MUTATORS = ['push', 'unshift', 'splice', 'pop', 'shift', 'fill', 'copyWithin', 'sort', 'reverse']

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
    if (hasAccessor(record, ['name', 'meta'])) {
      unreadable.push({ kind: 'route', file: rel(file), line: record.loc.start.line, problem: 'declares `name` or `meta` through a getter, whose value only the runtime has' })
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

  // `import { WidgetRegistry as WR }` registers just the same.
  const registryNames = new Set(['WidgetRegistry'])
  const namespaceNames = new Set()
  walk(ast, (node) => {
    if (node.type !== 'ImportDeclaration') return
    for (const specifier of node.specifiers) {
      if (specifier.type === 'ImportSpecifier' && (specifier.imported.name ?? specifier.imported.value) === 'WidgetRegistry') {
        registryNames.add(specifier.local.name)
      }
      if (specifier.type === 'ImportNamespaceSpecifier') namespaceNames.add(specifier.local.name)
    }
  })

  // A parameter or local of the same name is a different binding; the walk
  // skips any function that shadows it.
  walkOutsideShadow(ast, 'WidgetRegistry', (node) => {
    if (node.type !== 'CallExpression') return
    const callee = node.callee
    if (callee.type !== 'MemberExpression') return
    // `WidgetRegistry['register'](…)` is the same call.
    const method = callee.computed ? stringValue(callee.property) : callee.property.type === 'Identifier' ? callee.property.name : null
    const object = unwrap(callee.object)
    // `WidgetRegistry.register`, an aliased import, or a namespace import's
    // `RegistryModule.WidgetRegistry.register` — all the same registration.
    const isRegistry =
      (object.type === 'Identifier' && registryNames.has(object.name)) ||
      (object.type === 'MemberExpression' &&
        namespaceNames.has(unwrap(object.object).name) &&
        (object.computed ? stringValue(object.property) : object.property.name) === 'WidgetRegistry')
    if (method !== 'register' || !isRegistry) return

    const definition = unwrap(node.arguments[0])
    const readable =
      definition &&
      definition.type === 'ObjectExpression' &&
      !hasDynamicKey(definition) &&
      !hasAccessor(definition, ['type']) &&
      // A spread can carry — or replace — the type, exactly as in a route record.
      !definition.properties.some(
        (property, index) => property.type === 'SpreadElement' && index > definition.properties.findIndex((p) => propertyKey(p) === 'type')
      )
    const type = readable ? stringValue(ownProperty(definition, 'type')?.value) : null
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
  const constants = stringConstants(ast)
  walk(ast, (node) => {
    // `obj.helpId = 'x'` sets the same prop as `{ helpId: 'x' }`.
    if (node.type === 'AssignmentExpression' && node.operator === '=') {
      const target = node.left
      const assigned = stringValue(node.right)
      if (target.type !== 'MemberExpression' || assigned === null) return
      const member = target.computed
        ? staticKeyValue(unwrap(target.property), constants)
        : target.property.type === 'Identifier'
          ? target.property.name
          : null
      if (HELP_PROP_NAMES.includes(member)) {
        references.push({ helpId: assigned, file: rel(file), line: node.loc.start.line + lineOffset })
      }
      return
    }
    if (node.type === 'ObjectMethod' && node.kind === 'get' && HELP_PROP_NAMES.includes(propertyKey(node, constants))) {
      // Only a getter hides a value: an ordinary `helpId() {}` utility method
      // evaluates to a function and references no help id at all.
      unreadable.push({
        kind: 'reference',
        file: rel(file),
        line: node.loc.start.line + lineOffset,
        problem: 'declares a help id through a getter, whose value only the runtime has',
      })
      return
    }
    // `class X { helpId = 'a' }` supplies the same prop from an instance.
    if (node.type === 'ClassProperty' || node.type === 'ClassPrivateProperty' || node.type === 'PropertyDefinition') {
      const fieldName = node.computed ? staticKeyValue(unwrap(node.key), constants) : node.key.type === 'Identifier' ? node.key.name : null
      const fieldValue = stringValue(node.value)
      if (HELP_PROP_NAMES.includes(fieldName) && fieldValue !== null) {
        references.push({ helpId: fieldValue, file: rel(file), line: node.loc.start.line + lineOffset })
      }
      return
    }
    if (node.type !== 'ObjectProperty') return
    const key = propertyKey(node, constants)
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
    // Vue normalises `{ 'help-id': 'x' }` in a render function to the same
    // prop as `{ helpId: 'x' }`.
    if (!HELP_PROP_NAMES.includes(key) || value === null) return
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
        if (prop.type === 6 && HELP_PROP_NAMES.includes(prop.name) && prop.value) {
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
    if (!entry.isDirectory()) continue
    // Exactly one module ships: an extensionless import resolves to the first
    // extension in this order, so reading the others would invent widgets
    // from files the build never loads.
    const resolved = WIDGET_ENTRY_SUFFIXES.map((suffix) => join(widgetsDir, entry.name, `index${suffix}`)).find((file) =>
      statSync(file, { throwIfNoEntry: false })
    )
    if (resolved) collectWidgets(resolved)
  }
}

for (const dir of REFERENCE_DIRS) for (const file of sourceFiles(join(SCAN_ROOT, dir))) collectReferences(file)

process.stdout.write(JSON.stringify({ routes, widgets, references, unreadable }) + '\n')
