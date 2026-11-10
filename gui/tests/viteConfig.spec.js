import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse } from '@babel/parser'

/**
 * The dev server has to forward every backend path the Admin GUI can hand out,
 * not just the ones it fetches itself. The webhook binding form builds its call
 * URL from `window.location.origin` (#1256) — correct in production, where
 * FastAPI serves the SPA, but in dev that origin is this dev server. Without a
 * `/hook` proxy the copied URL silently hits Vite's SPA fallback and the
 * backend never sees the call, which looks exactly like a broken adapter.
 *
 * The config cannot simply be imported here: it reads package.json relative to
 * `import.meta.url` at module scope, which vitest does not serve as a file URL.
 * It is parsed instead, so a prefix mentioned only in a comment cannot pass.
 */
function proxyTargets() {
  const source = readFileSync(resolve(import.meta.dirname, '../vite.config.js'), 'utf8')
  const ast = parse(source, { sourceType: 'module' })
  const targets = {}

  const walk = (node) => {
    if (!node || typeof node !== 'object') return
    if (node.type === 'ObjectProperty' && keyName(node) === 'proxy' && node.value.type === 'ObjectExpression') {
      for (const entry of node.value.properties) {
        if (entry.type !== 'ObjectProperty' || entry.value.type !== 'ObjectExpression') continue
        const options = {}
        for (const option of entry.value.properties) {
          if (option.type !== 'ObjectProperty') continue
          options[keyName(option)] = option.value.value
        }
        targets[keyName(entry)] = options
      }
    }
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) value.forEach(walk)
      else walk(value)
    }
  }
  walk(ast.program)
  return targets
}

function keyName(property) {
  return property.key.type === 'Identifier' ? property.key.name : property.key.value
}

describe('gui vite dev server proxy', () => {
  const proxy = proxyTargets()

  it.each(['/api', '/hook', '/help'])('forwards %s to the backend', (prefix) => {
    expect(proxy[prefix]).toBeDefined()
    expect(proxy[prefix].target).toBe('http://localhost:8080')
  })

  it('keeps the websocket upgrade on /api', () => {
    expect(proxy['/api'].ws).toBe(true)
  })

  it('still sends /visu to the Visu dev server', () => {
    expect(proxy['/visu'].target).toBe('http://localhost:5174')
  })
})
