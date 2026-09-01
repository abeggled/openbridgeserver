// Regression tests for scan-help-declarations.mjs.
//
// Nearly every case below is a finding from the review of #1183, back when
// this scan was regexes over raw text. They are kept as tests not because a
// parser is likely to regress on them, but because they record what the text
// scan could not do — and because a future change back towards pattern
// matching would break them immediately.
//
// Run via `node --test tools/scan-help-declarations.test.mjs`.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { tmpdir } from 'node:os'

import { fileURLToPath } from 'node:url'

const SCANNER = resolve(fileURLToPath(new URL('scan-help-declarations.mjs', import.meta.url)))

/** Run the scanner over a throwaway tree of `{ 'path/to/file': contents }`. */
function scan(files) {
  const root = mkdtempSync(join(tmpdir(), 'obs-scan-'))
  try {
    for (const [path, contents] of Object.entries(files)) {
      const full = join(root, path)
      mkdirSync(dirname(full), { recursive: true })
      writeFileSync(full, contents)
    }
    return JSON.parse(execFileSync('node', [SCANNER, '--root', root], { encoding: 'utf-8' }))
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}

const router = (body) => ({ 'gui/src/router/index.js': `const routes = [\n${body}\n]\nexport default routes\n` })
const widget = (body) => ({ 'frontend/src/widgets/Probe/index.ts': body })
const view = (body) => ({ 'gui/src/views/ProbeView.vue': body })

const names = (result) => result.routes.map((route) => route.name)
const helpIds = (result) => result.references.map((reference) => reference.helpId).sort()
const problems = (result) => result.unreadable.map((spot) => spot.problem)

// ── Routes ──────────────────────────────────────────────────────────────────

test('a route declares its name and meta.helpId', () => {
  const result = scan(router("  { path: '/', name: 'Dashboard', meta: { helpId: 'dashboard' } },"))

  assert.deepEqual(result.routes.map((r) => [r.name, r.helpId]), [['Dashboard', 'dashboard']])
})

test('a quoted property key declares just as well', () => {
  const result = scan(router(`  { path: '/', "name": "Dashboard", 'meta': { "helpId": 'dashboard' } },`))

  assert.deepEqual(result.routes.map((r) => [r.name, r.helpId]), [['Dashboard', 'dashboard']])
})

test('nested children are enumerated, and a parent neither borrows nor hides their declarations', () => {
  const result = scan(router("  { path: '/g', children: [\n    { path: 'a', name: 'A', meta: { helpId: 'a' } },\n    { path: 'b', name: 'B' },\n  ] },"))

  assert.deepEqual(result.routes.map((r) => [r.name, r.helpId]).sort(), [['A', 'a'], ['B', null]])
})

test('a name inside another property is not a route', () => {
  const result = scan(router("  { path: '/', name: 'Dashboard', props: { name: 'not-a-route' }, meta: { helpId: 'dashboard' } },"))

  assert.deepEqual(names(result), ['Dashboard'])
})

test('a helpId nested inside meta declares nothing — TopBar reads meta.helpId directly', () => {
  const result = scan(router("  { path: '/', name: 'Dashboard', meta: { analytics: { helpId: 'dashboard' } } },"))

  assert.deepEqual(result.routes.map((r) => r.helpId), [null])
})

test('string text shaped like a property declares nothing', () => {
  const result = scan(router(`  { path: '/', name: 'Dashboard', meta: { note: "helpId: 'dashboard'" } },`))

  assert.deepEqual(result.routes.map((r) => r.helpId), [null])
})

test('an unnamed record is skipped, not reported', () => {
  const result = scan(router("  { path: '/:pathMatch(.*)*', redirect: '/' },"))

  assert.deepEqual(names(result), [])
  assert.deepEqual(problems(result), [])
})

for (const [label, body] of [
  ['a name from a constant', "  { path: '/c', name: EXTRA_NAME },"],
  ['a shorthand name', '  { path: `/s`, name },'],
]) {
  test(`fails closed on ${label}`, () => {
    const result = scan(router(body))

    assert.equal(names(result).length, 0)
    assert.match(problems(result)[0], /`name` is not a string literal/)
  })
}

test('a computed but static key declares just as well', () => {
  const result = scan(router("  { path: '/', ['name']: 'ComputedName', ['meta']: { ['helpId']: 'dashboard' } },"))

  assert.deepEqual(result.routes.map((r) => [r.name, r.helpId]), [['ComputedName', 'dashboard']])
})

test('fails closed on a computed key only the runtime knows', () => {
  const result = scan(router("  { path: '/', [KEY]: 'X', name: 'A', meta: { helpId: 'a' } },"))

  assert.deepEqual(names(result), [])
  assert.match(problems(result)[0], /computed property key the gate cannot resolve/)
})

test('fails closed on a widget registration with a computed key', () => {
  const result = scan(widget("WidgetRegistry.register({ [KEY]: 1, type: 'Slider' })\n"))

  assert.deepEqual(result.widgets, [])
  assert.match(problems(result)[0], /`type` is not a string literal/)
})

test('a computed helpId key is still a reference', () => {
  const result = scan({ 'gui/src/probe.js': "const m = { ['helpId']: 'logs-level' }\n" })

  assert.deepEqual(helpIds(result), ['logs-level'])
})

test('fails closed on a spread in the route array', () => {
  const result = scan(router('  ...EXTRA_ROUTES,'))

  assert.match(problems(result)[0], /contributes routes the gate cannot read/)
})

test('fails closed on a spread inside a route record', () => {
  const result = scan(router("  { path: '/', name: 'A', ...override, meta: { helpId: 'a' } },"))

  assert.match(problems(result)[0], /spreads into a route record/)
})

test('fails closed on children that are not an inline array', () => {
  const result = scan(router("  { path: '/g', children: EXTERNAL, props: ['a'] },"))

  assert.match(problems(result)[0], /`children` the gate cannot read/)
})

test('a spread inside a route value stays legal', () => {
  const result = scan(router("  { path: '/', name: 'A', meta: { ...base, helpId: 'a' } },"))

  assert.deepEqual(result.routes.map((r) => [r.name, r.helpId]), [['A', 'a']])
})

test('a `routes` inside a helper function is not the router table', () => {
  const files = {
    'gui/src/router/index.js': "const routes = [\n  { path: '/', name: 'Dashboard', meta: { helpId: 'dashboard' } },\n]\nfunction helper() {\n  const routes = []\n  return routes\n}\nexport default routes\n",
  }

  assert.deepEqual(names(scan(files)), ['Dashboard'])
})

test('a spread before the helpId leaves the literal winning', () => {
  const result = scan(router("  { path: '/', name: 'A', meta: { ...base, helpId: 'a' } },"))

  assert.deepEqual(result.routes.map((r) => [r.name, r.helpId]), [['A', 'a']])
})

for (const [label, meta] of [
  ['a spread after the helpId', "{ helpId: 'a', ...base }"],
  ['a computed key after the helpId', "{ helpId: 'a', [k]: 1 }"],
  ['no literal helpId at all', '{ ...base }'],
]) {
  test(`fails closed on meta with ${label}`, () => {
    const result = scan(router(`  { path: '/', name: 'A', meta: ${meta} },`))

    assert.deepEqual(names(result), [])
    assert.match(problems(result)[0], /`meta`/)
  })
}

test('fails closed on a string assigned through a dynamic key', () => {
  // The key may be `helpId` at runtime, and then this is a live button.
  const result = scan({ 'gui/src/probe.js': "const m = { [reviewKey]: 'missing-dynamic-computed-help' }\n" })

  assert.deepEqual(helpIds(result), [])
  assert.match(problems(result)[0], /computed key the gate cannot resolve/)
})

test('a dynamic key with a non-string value is not a help reference', () => {
  assert.deepEqual(problems(scan({ 'gui/src/probe.js': 'const m = { [key]: 42 }\n' })), [])
})

test('a similarly named array declared earlier is not the router table', () => {
  const files = {
    'gui/src/router/index.js': "const routesBackup = [{ path: '/o', name: 'Backup' }]\nconst routes = [\n  { path: '/', name: 'Dashboard', meta: { helpId: 'dashboard' } },\n]\n",
  }

  assert.deepEqual(names(scan(files)), ['Dashboard'])
})

test('an exported routes declaration is the router table', () => {
  const files = { 'gui/src/router/index.js': "export const routes = [\n  { path: '/', name: 'Dashboard', meta: { helpId: 'dashboard' } },\n]\n" }

  assert.deepEqual(names(scan(files)), ['Dashboard'])
})

test('a template literal without interpolation is a string value', () => {
  const result = scan(router('  { path: `/`, name: `Dashboard`, meta: { helpId: `dashboard` } },'))

  assert.deepEqual(result.routes.map((r) => [r.name, r.helpId]), [['Dashboard', 'dashboard']])
})

test('a duplicated key resolves to the last one, as it does at runtime', () => {
  const result = scan(router("  { path: '/', name: 'A', meta: { helpId: 'first', helpId: 'second' } },"))

  assert.deepEqual(result.routes.map((r) => r.helpId), ['second'])
})

test('fails closed on a getter-backed route name', () => {
  const result = scan(router("  { path: '/', get name() { return 'A' }, meta: { helpId: 'a' } },"))

  assert.deepEqual(names(result), [])
  assert.match(problems(result)[0], /through a getter/)
})

test('fails closed when routes are appended after the initialiser', () => {
  const files = {
    'gui/src/router/index.js': "const routes = [\n  { path: '/', name: 'A', meta: { helpId: 'a' } },\n]\nroutes.push(...extra)\n",
  }

  assert.match(problems(scan(files))[0], /mutates the routes array with push\(\)/)
})

test('a template literal used as a key resolves like a quoted one', () => {
  const result = scan(router('  { path: `/`, [`name`]: `TplKey`, meta: { [`helpId`]: `a` } },'))

  assert.deepEqual(result.routes.map((r) => [r.name, r.helpId]), [['TplKey', 'a']])
})

test('fails closed on a computed getter-backed name', () => {
  const result = scan(router("  { path: '/', get ['name']() { return 'G' }, meta: { helpId: 'a' } },"))

  assert.deepEqual(names(result), [])
  assert.match(problems(result)[0], /through a getter/)
})

test('a helper with its own routes variable is not the router table', () => {
  const files = {
    'gui/src/router/index.js':
      "const routes = [\n  { path: '/', name: 'A', meta: { helpId: 'a' } },\n]\nfunction helper() { const routes = []; routes.push(1); return routes }\n",
  }

  assert.deepEqual(problems(scan(files)), [])
  assert.deepEqual(names(scan(files)), ['A'])
})

test('concat leaves the table alone and is not a mutation', () => {
  const files = { 'gui/src/router/index.js': "const routes = [\n  { path: '/', name: 'A', meta: { helpId: 'a' } },\n]\nconst merged = routes.concat(extra)\n" }

  assert.deepEqual(problems(scan(files)), [])
})

test('fails closed when the routes table is reassigned', () => {
  const files = { 'gui/src/router/index.js': "let routes = [\n  { path: '/', name: 'A', meta: { helpId: 'a' } },\n]\nroutes = other\n" }

  assert.match(problems(scan(files))[0], /reassigns the routes table/)
})

test('fails closed when the routes table is assigned into by index', () => {
  const files = { 'gui/src/router/index.js': "const routes = [\n  { path: '/', name: 'A', meta: { helpId: 'a' } },\n]\nroutes[0] = other\n" }

  assert.match(problems(scan(files))[0], /assigns into the routes table/)
})

test('fails closed on a getter under a computed key the parse cannot resolve', () => {
  const result = scan(router('  { path: `/`, get [runtimeKey]() { return `G` }, meta: { helpId: `a` } },'))

  assert.deepEqual(names(result), [])
  assert.match(problems(result)[0], /computed property key the gate cannot resolve/)
})

test('the table handed to createRouter is the one that counts', () => {
  const files = {
    'gui/src/router/index.js':
      "const routes = [{ path: '/', name: 'Decoy' }]\nconst realTable = [{ path: '/r', name: 'Real', meta: { helpId: 'a' } }]\nexport default createRouter({ history: h, routes: realTable })\n",
  }

  assert.deepEqual(names(scan(files)), ['Real'])
})

test('a plain `routes` table is still read when createRouter names nothing else', () => {
  const files = { 'gui/src/router/index.js': "const routes = [{ path: '/', name: 'A', meta: { helpId: 'a' } }]\nexport default createRouter({ routes })\n" }

  assert.deepEqual(names(scan(files)), ['A'])
})

test('a duplicated routes option resolves to the last one', () => {
  const files = {
    'gui/src/router/index.js':
      "const first = [{ path: '/1', name: 'First' }]\nconst second = [{ path: '/2', name: 'Second', meta: { helpId: 'a' } }]\nexport default createRouter({ routes: first, routes: second })\n",
  }

  assert.deepEqual(names(scan(files)), ['Second'])
})

test('fails closed when a spread can replace the routes option', () => {
  const files = {
    'gui/src/router/index.js': "const routes = [{ path: '/', name: 'A', meta: { helpId: 'a' } }]\nexport default createRouter({ routes, ...overrides })\n",
  }

  assert.deepEqual(names(scan(files)), [])
  assert.match(problems(scan(files))[0], /`routes` can be replaced after it/)
})

test('a spread before the routes option leaves it winning', () => {
  const files = {
    'gui/src/router/index.js': "const routes = [{ path: '/', name: 'A', meta: { helpId: 'a' } }]\nexport default createRouter({ ...defaults, routes })\n",
  }

  assert.deepEqual(names(scan(files)), ['A'])
})

test('createRouter options handed through an identifier still name the table', () => {
  const files = {
    'gui/src/router/index.js':
      "const table = [{ path: '/', name: 'ViaOptions', meta: { helpId: 'a' } }]\nconst options = { history: h, routes: table }\nexport default createRouter(options)\n",
  }

  assert.deepEqual(names(scan(files)), ['ViaOptions'])
})

test('an aliased createRouter import still names the table', () => {
  const files = {
    'gui/src/router/index.js':
      "import { createRouter as make } from 'vue-router'\nconst table = [{ path: '/', name: 'Aliased', meta: { helpId: 'a' } }]\nexport default make({ routes: table })\n",
  }

  assert.deepEqual(names(scan(files)), ['Aliased'])
})

test('fails closed when the options object routes are assigned afterwards', () => {
  const files = {
    'gui/src/router/index.js':
      "const table = [{ path: '/', name: 'A', meta: { helpId: 'a' } }]\nconst options = { routes: table }\noptions.routes = other\nexport default createRouter(options)\n",
  }

  assert.deepEqual(names(scan(files)), [])
  assert.match(problems(scan(files))[0], /router options `routes` after they are written/)
})

test('an inline routes array is the table, not a similarly named declaration', () => {
  const files = {
    'gui/src/router/index.js':
      "const routes = [{ path: '/decoy', name: 'Decoy' }]\nexport default createRouter({ routes: [{ path: '/', name: 'Inline', meta: { helpId: 'a' } }] })\n",
  }

  assert.deepEqual(names(scan(files)), ['Inline'])
})

test('the first createRouter call is the production router', () => {
  const files = {
    'gui/src/router/index.js':
      "const main = [{ path: '/', name: 'Main', meta: { helpId: 'a' } }]\nexport default createRouter({ routes: main })\nconst aux = [{ path: '/x', name: 'Aux' }]\nexport const other = createRouter({ routes: aux })\n",
  }

  assert.deepEqual(names(scan(files)), ['Main'])
})

test('an options mutation after the router is built cannot change it', () => {
  const files = {
    'gui/src/router/index.js':
      "const table = [{ path: '/', name: 'A', meta: { helpId: 'a' } }]\nconst options = { routes: table }\nexport default createRouter(options)\noptions.routes = later\n",
  }

  assert.deepEqual(names(scan(files)), ['A'])
  assert.deepEqual(problems(scan(files)), [])
})

test('a route added through addRoute is a live route', () => {
  const files = {
    'gui/src/router/index.js':
      "const routes = [{ path: '/', name: 'Base', meta: { helpId: 'a' } }]\nconst router = createRouter({ routes })\nrouter.addRoute({ path: '/late', name: 'Added' })\nexport default router\n",
  }

  assert.deepEqual(scan(files).routes.map((r) => [r.name, r.helpId]).sort(), [['Added', null], ['Base', 'a']])
})

test('addRoute on an unrelated object is not a route', () => {
  const files = {
    'gui/src/router/index.js':
      "const routes = [{ path: '/', name: 'Base', meta: { helpId: 'a' } }]\nconst router = createRouter({ routes })\nconst preview = { addRoute(r) {} }\npreview.addRoute({ path: '/x', name: 'NotLive' })\nrouter.addRoute({ path: '/y', name: 'Live' })\nexport default router\n",
  }

  assert.deepEqual(names(scan(files)).sort(), ['Base', 'Live'])
})

test('addRoute on an auxiliary router is not a production route', () => {
  const files = {
    'gui/src/router/index.js':
      "const routes = [{ path: '/', name: 'Base', meta: { helpId: 'a' } }]\nconst router = createRouter({ routes })\nconst aux = createRouter({ routes: [] })\naux.addRoute({ path: '/x', name: 'AuxOnly' })\nrouter.addRoute({ path: '/y', name: 'Live' })\nexport default router\n",
  }

  assert.deepEqual(names(scan(files)).sort(), ['Base', 'Live'])
})

test('fails closed on an addRoute the gate cannot read', () => {
  const files = { 'gui/src/router/index.js': "const routes = []\ncreateRouter({ routes }).addRoute(extra)\n" }

  assert.match(problems(scan(files))[0], /adds a route the gate cannot read/)
})

// ── Widgets ─────────────────────────────────────────────────────────────────

test('every registration in a module is enumerated', () => {
  const result = scan(widget("WidgetRegistry.register({ type: 'Slider' })\nWidgetRegistry.register({ type: 'SliderPro' })\n"))

  assert.deepEqual(result.widgets.map((w) => w.type), ['Slider', 'SliderPro'])
})

test('whitespace in the registration call does not hide it', () => {
  const result = scan(widget("WidgetRegistry\n  .register({ type: 'Slider' })\n"))

  assert.deepEqual(result.widgets.map((w) => w.type), ['Slider'])
})

test("a nested config's type is not the registered type", () => {
  const result = scan(widget("WidgetRegistry.register({ type: EXTRA, defaultConfig: { type: 'Slider' } })\n"))

  assert.deepEqual(result.widgets, [])
  assert.match(problems(result)[0], /`type` is not a string literal/)
})

test('a commented-out registration registers nothing', () => {
  const result = scan(widget("// WidgetRegistry.register({ type: 'Ghost' })\nWidgetRegistry.register({ type: 'Slider' })\n"))

  assert.deepEqual(result.widgets.map((w) => w.type), ['Slider'])
  assert.deepEqual(problems(result), [])
})

test('TypeScript syntax in a widget module is parsed, not choked on', () => {
  const body = "import type { Foo } from './types'\nconst config: Record<string, number> = { a: 1 }\nWidgetRegistry.register({ type: 'Slider', defaultConfig: config } as never)\n"

  assert.deepEqual(scan(widget(body)).widgets.map((w) => w.type), ['Slider'])
})

test('a computed register call is the same registration', () => {
  const result = scan(widget("WidgetRegistry['register']({ type: 'Slider' })\n"))

  assert.deepEqual(result.widgets.map((w) => w.type), ['Slider'])
})

test('fails closed on a spread after the widget type', () => {
  const result = scan(widget("WidgetRegistry.register({ type: 'Slider', ...overrides })\n"))

  assert.deepEqual(result.widgets, [])
  assert.match(problems(result)[0], /`type` is not a string literal/)
})

test('a spread before the widget type leaves the literal winning', () => {
  const result = scan(widget("WidgetRegistry.register({ ...defaults, type: 'Slider' })\n"))

  assert.deepEqual(result.widgets.map((w) => w.type), ['Slider'])
})

test('an aliased WidgetRegistry import registers just the same', () => {
  const result = scan(widget("import { WidgetRegistry as WR } from '../registry'\nWR.register({ type: 'Aliased' })\n"))

  assert.deepEqual(result.widgets.map((w) => w.type), ['Aliased'])
})

test('a parameter shadowing WidgetRegistry is a different binding', () => {
  const body = "import { WidgetRegistry } from '../registry'\nfunction make(WidgetRegistry) { WidgetRegistry.register({ type: 'Shadowed' }) }\nWidgetRegistry.register({ type: 'Real' })\n"

  assert.deepEqual(scan(widget(body)).widgets.map((w) => w.type), ['Real'])
})

test('a namespace import registers just the same', () => {
  const body = "import * as RegistryModule from '../registry'\nRegistryModule.WidgetRegistry.register({ type: 'Namespaced' })\n"

  assert.deepEqual(scan(widget(body)).widgets.map((w) => w.type), ['Namespaced'])
})

test('a widget module in any supported dialect is read', () => {
  const files = {
    'frontend/src/widgets/A/index.js': "WidgetRegistry.register({ type: 'FromJs' })\n",
    'frontend/src/widgets/B/index.tsx': "WidgetRegistry.register({ type: 'FromTsx' })\n",
  }

  assert.deepEqual(scan(files).widgets.map((w) => w.type).sort(), ['FromJs', 'FromTsx'])
})

test('only the entry module the resolver loads is read', () => {
  // An extensionless import takes one file; reading the others would invent
  // widgets from modules the build never loads.
  const files = {
    'frontend/src/widgets/A/index.js': "WidgetRegistry.register({ type: 'Wins' })\n",
    'frontend/src/widgets/A/index.ts': "WidgetRegistry.register({ type: 'Loses' })\n",
  }

  assert.deepEqual(scan(files).widgets.map((w) => w.type), ['Wins'])
})

test('a parameter shadowing an aliased registry is a different binding', () => {
  const body = "import { WidgetRegistry as WR } from '../registry'\nfunction make(WR) { WR.register({ type: 'Shadowed' }) }\nWR.register({ type: 'Real' })\n"

  assert.deepEqual(scan(widget(body)).widgets.map((w) => w.type), ['Real'])
})

test('an optional registration call is the same call', () => {
  const body = "import { WidgetRegistry } from '../registry'\nWidgetRegistry?.register({ type: 'Optional' })\n"

  assert.deepEqual(scan(widget(body)).widgets.map((w) => w.type), ['Optional'])
})

test('an optional namespace registration is the same call', () => {
  const body = "import * as R from '../registry'\nR?.WidgetRegistry?.register({ type: 'OptionalNs' })\n"

  assert.deepEqual(scan(widget(body)).widgets.map((w) => w.type), ['OptionalNs'])
})

test('a block-scoped shadow is a different binding', () => {
  const body =
    "import { WidgetRegistry } from '../registry'\n{ const WidgetRegistry = fake; WidgetRegistry.register({ type: 'BlockShadowed' }) }\nWidgetRegistry.register({ type: 'Real' })\n"

  assert.deepEqual(scan(widget(body)).widgets.map((w) => w.type), ['Real'])
})

test('a top-level local named WidgetRegistry is not the imported registry', () => {
  const body =
    "import { WidgetRegistry as WR } from '../registry'\nconst WidgetRegistry = { register() {} }\nWidgetRegistry.register({ type: 'LocalFake' })\nWR.register({ type: 'Real' })\n"

  assert.deepEqual(scan(widget(body)).widgets.map((w) => w.type), ['Real'])
})

test('a WidgetRegistry imported from an unrelated module is a different object', () => {
  const body = "import { WidgetRegistry } from '@/some/other/module'\nWidgetRegistry.register({ type: 'Unrelated' })\n"

  assert.deepEqual(scan(widget(body)).widgets, [])
})

test('a registration in a module the entry imports is found', () => {
  const files = {
    'frontend/src/widgets/A/index.ts': "import './extra'\nWidgetRegistry.register({ type: 'Main' })\n",
    'frontend/src/widgets/A/extra.ts': "WidgetRegistry.register({ type: 'FromHelper' })\n",
  }

  assert.deepEqual(scan(files).widgets.map((w) => w.type).sort(), ['FromHelper', 'Main'])
})

test('an imported component is read as an SFC, not as a script', () => {
  const files = {
    'frontend/src/widgets/A/index.ts': "import Widget from './Widget.vue'\nWidgetRegistry.register({ type: 'Main' })\n",
    'frontend/src/widgets/A/Widget.vue': "<template><div /></template>\n<script setup>WidgetRegistry.register({ type: 'FromSfc' })</script>\n",
  }

  const result = scan(files)

  assert.deepEqual(result.widgets.map((w) => w.type).sort(), ['FromSfc', 'Main'])
  assert.deepEqual(problems(result), [])
})

test('an import cycle between widget modules terminates', () => {
  const files = {
    'frontend/src/widgets/A/index.ts': "import './extra'\nWidgetRegistry.register({ type: 'Main' })\n",
    'frontend/src/widgets/A/extra.ts': "import './index'\n",
  }

  assert.deepEqual(scan(files).widgets.map((w) => w.type), ['Main'])
})

test('a type-only edge is erased and not followed', () => {
  const files = {
    'frontend/src/widgets/A/index.ts': "export type { T } from './type-only'\nWidgetRegistry.register({ type: 'Main' })\n",
    'frontend/src/widgets/A/type-only.ts': "WidgetRegistry.register({ type: 'TypeOnly' })\n",
  }

  assert.deepEqual(scan(files).widgets.map((w) => w.type), ['Main'])
})

test('a specifier-level type import is erased and not followed', () => {
  const files = {
    'frontend/src/widgets/A/index.ts': "import { type T } from './type-only'\nWidgetRegistry.register({ type: 'Main' })\n",
    'frontend/src/widgets/A/type-only.ts': "WidgetRegistry.register({ type: 'TypeOnly' })\n",
  }

  assert.deepEqual(scan(files).widgets.map((w) => w.type), ['Main'])
})

test('a namespace import from an unrelated module is not the registry', () => {
  const body = "import * as utils from '@/utils/helpers'\nutils.WidgetRegistry.register({ type: 'NotRegistry' })\n"

  assert.deepEqual(scan(widget(body)).widgets, [])
})

test('a dynamically imported module executes and is followed', () => {
  const files = {
    'frontend/src/widgets/A/index.ts': "void import('./dynamic')\nWidgetRegistry.register({ type: 'Main' })\n",
    'frontend/src/widgets/A/dynamic.ts': "WidgetRegistry.register({ type: 'Dynamic' })\n",
  }

  assert.deepEqual(scan(files).widgets.map((w) => w.type).sort(), ['Dynamic', 'Main'])
})

test('a stylesheet the entry imports is not parsed as a module', () => {
  const files = {
    'frontend/src/widgets/A/index.ts': "import './style.css'\nWidgetRegistry.register({ type: 'Styled' })\n",
    'frontend/src/widgets/A/style.css': '.a { color: red }\n',
  }

  const result = scan(files)

  assert.deepEqual(result.widgets.map((w) => w.type), ['Styled'])
  assert.deepEqual(problems(result), [])
})

// ── References ──────────────────────────────────────────────────────────────

for (const [label, attribute] of [
  ['double quoted', 'help-id="logs-level"'],
  ['single quoted', "help-id='logs-level'"],
  ['unquoted', 'help-id=logs-level'],
  ['spaced around the equals sign', 'help-id = "logs-level"'],
]) {
  test(`a ${label} static attribute is a reference`, () => {
    const result = scan(view(`<template>\n  <HelpButton ${attribute} />\n</template>\n`))

    assert.deepEqual(helpIds(result), ['logs-level'])
  })
}

test('a camelCase prop spelling is the same attribute', () => {
  const result = scan(view('<template>\n  <HelpButton helpId="logs-level" />\n</template>\n'))

  assert.deepEqual(helpIds(result), ['logs-level'])
})

test('a kebab-case prop in a render function is a reference', () => {
  const result = scan({ 'gui/src/render.js': "const vnode = h(HelpButton, { 'help-id': 'logs-level' })\n" })

  assert.deepEqual(helpIds(result), ['logs-level'])
})

for (const operator of ['||=', '??=', '&&=']) {
  test(`a helpId assigned with ${operator} is a reference`, () => {
    const result = scan({ 'gui/src/probe.js': `props.helpId ${operator} 'logs-level'\n` })

    assert.deepEqual(helpIds(result), ['logs-level'])
  })
}

test('a helpId assigned to a property is a reference', () => {
  const result = scan({ 'gui/src/assign.js': "props.helpId = 'logs-level'\n" })

  assert.deepEqual(helpIds(result), ['logs-level'])
})

test('a bound attribute whose expression is a literal is resolvable', () => {
  const result = scan(view('<template>\n  <HelpButton :help-id="\'logs-level\'" />\n</template>\n'))

  assert.deepEqual(helpIds(result), ['logs-level'])
})

test('an external SFC script is the component\'s script', () => {
  const files = {
    'gui/src/views/P.vue': '<template><div /></template>\n<script src="./logic.js"></script>\n',
    'gui/src/views/logic.js': "const m = { helpId: 'logs-level' }\n",
  }

  assert.deepEqual([...new Set(helpIds(scan(files)))], ['logs-level'])
})

test('a bound attribute is not resolvable and is skipped', () => {
  const result = scan(view('<template>\n  <HelpButton :help-id="helpId" />\n</template>\n'))

  assert.deepEqual(helpIds(result), [])
})

test('a helpId property in script is a reference', () => {
  const result = scan(view("<template><div /></template>\n<script setup>\nconst m = { helpId: 'logs-level' }\n</script>\n"))

  assert.deepEqual(helpIds(result), ['logs-level'])
})

test('a CSS custom property shaped like a JS one is not a reference', () => {
  const result = scan(view("<template><div /></template>\n<style>\n.panel { --panel-helpId: 'gone'; }\n</style>\n"))

  assert.deepEqual(helpIds(result), [])
})

test('property-shaped prose rendered in a template is not a reference', () => {
  const result = scan(view("<template>\n  <p>Set helpId: 'gone' in meta</p>\n</template>\n"))

  assert.deepEqual(helpIds(result), [])
})

test('a custom element named like script is not a script block', () => {
  const result = scan(view("<template>\n  <script-editor>Set helpId: 'gone' here</script-editor>\n</template>\n"))

  assert.deepEqual(helpIds(result), [])
})

for (const [label, code] of [
  ['a line comment', "// { helpId: 'gone' }"],
  ['a comment right after a string', "const removed = 'old'// { helpId: 'gone' }"],
  ['a block comment', "/* { helpId: 'gone' } */"],
  ['string prose', `const msg = "set helpId: 'gone' in meta"`],
  ['template literal text', "const t = `write helpId: 'gone' here`"],
  ['a ternary operand', "const pick = true ? 'helpId' : 'gone'"],
  ['a regex literal', "const q = /helpId: 'gone'/"],
]) {
  test(`${label} is not a reference`, () => {
    assert.deepEqual(helpIds(scan({ 'gui/src/probe.js': `${code}\n` })), [])
  })
}

for (const [label, code] of [
  ['after a regex holding a quote', "const q = /['\"]/\nconst m = { helpId: 'logs-level' }"],
  ['after an arrow-function regex', "const q = () => /'/\nconst m = { helpId: 'logs-level' }"],
  ['after a division', 'const r = width / height\nconst m = { helpId: \'logs-level\' }'],
  ['inside a template expression', "const t = `${'}' && ({ helpId: 'logs-level' }).helpId}`"],
  ['beside a protocol-relative URL', `const u = '//cdn.example.com'\nconst m = { helpId: 'logs-level' }`],
]) {
  test(`a real declaration ${label} is still found`, () => {
    assert.deepEqual(helpIds(scan({ 'gui/src/probe.js': `${code}\n` })), ['logs-level'])
  })
}

test('fails closed on a getter-backed help reference', () => {
  const result = scan({ 'gui/src/probe.js': "const m = { get helpId() { return 'gone' } }\n" })

  assert.deepEqual(helpIds(result), [])
  assert.match(problems(result)[0], /through a getter/)
})

test('a computed key resolves through a module-level constant', () => {
  const body = "const reviewKey = 'helpId'\nconst props = {}\nprops[reviewKey] = 'logs-level'\n"

  assert.deepEqual(helpIds(scan({ 'gui/src/probe.js': body })), ['logs-level'])
})

test('an ordinary keyed write is not a help reference and does not fail the gate', () => {
  // `busy[a.id] = 'test'` is how normal code writes into a map; refusing to
  // read it would fail the gate over code with nothing to do with help.
  const result = scan({ 'gui/src/probe.js': "const busy = {}\nbusy[a.id] = 'test'\n" })

  assert.deepEqual(helpIds(result), [])
  assert.deepEqual(problems(result), [])
})

test('an ordinary method named helpId is not a help reference', () => {
  const result = scan({ 'gui/src/probe.js': 'const m = { helpId() { return 1 } }\n' })

  assert.deepEqual(helpIds(result), [])
  assert.deepEqual(problems(result), [])
})

test('a help id stored in a class field is a reference', () => {
  const result = scan({ 'gui/src/probe.js': "class ReviewHelp { helpId = 'logs-level' }\n" })

  assert.deepEqual(helpIds(result), ['logs-level'])
})

test('a class getter hides its help id and fails closed', () => {
  const result = scan({ 'gui/src/probe.js': "class R { get helpId() { return 'gone' } }\n" })

  assert.deepEqual(helpIds(result), [])
  assert.match(problems(result)[0], /through a class getter/)
})

test('an ordinary class method named helpId is quiet', () => {
  const result = scan({ 'gui/src/probe.js': 'class R { helpId() { return 1 } }\n' })

  assert.deepEqual(helpIds(result), [])
  assert.deepEqual(problems(result), [])
})

test('every supported extension is scanned in both frontends', () => {
  const files = {}
  for (const suffix of ['js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs', 'mts', 'cts']) files[`gui/src/a${suffix}.${suffix}`] = "const m = { helpId: 'logs-level' }\n"
  files['frontend/src/probe.ts'] = "const m = { helpId: 'datapoints' }\n"

  assert.deepEqual([...new Set(helpIds(scan(files)))].sort(), ['datapoints', 'logs-level'])
})

test('a co-located test module is not scanned', () => {
  // Its fixtures are never bundled, so a help-shaped one is not a live button.
  const files = {
    'frontend/src/probe.test.ts': "const m = { helpId: 'fixture-only' }\n",
    'frontend/src/probe.ts': "const m = { helpId: 'real-ref' }\n",
  }

  assert.deepEqual(helpIds(scan(files)), ['real-ref'])
})

test('an earlier duplicate helpId is replaced and is not a reference', () => {
  const result = scan({ 'gui/src/probe.js': "const m = { helpId: 'dead', helpId: 'logs-level' }\n" })

  assert.deepEqual(helpIds(result), ['logs-level'])
})

test('an external SFC template is followed', () => {
  const files = {
    'gui/src/views/Probe.vue': '<template src="./Probe.html"></template>\n<script setup>const a = 1</script>\n',
    'gui/src/views/Probe.html': '<div><HelpButton help-id="logs-level" /></div>\n',
  }

  assert.deepEqual(helpIds(scan(files)), ['logs-level'])
})

test('an external template that cannot be read fails closed', () => {
  const files = { 'gui/src/views/Probe.vue': '<template src="./Missing.html"></template>\n' }

  assert.match(problems(scan(files))[0], /template at \.\/Missing\.html the gate cannot read/)
})

test('node_modules is not scanned', () => {
  const files = { 'gui/src/node_modules/pkg/index.js': "const m = { helpId: 'gone' }\n" }

  assert.deepEqual(helpIds(scan(files)), [])
})

test('a reference reports the line it sits on', () => {
  const result = scan(view("<template>\n  <!--\n    old\n  -->\n  <HelpButton help-id=\"logs-level\" />\n</template>\n"))

  assert.deepEqual(result.references.map((r) => r.line), [5])
})

test('a file that cannot be parsed is reported, not silently skipped', () => {
  const result = scan({ 'gui/src/broken.js': 'const = = =\n' })

  assert.equal(result.unreadable.length, 1)
  assert.match(result.unreadable[0].problem, /cannot be parsed/)
})
