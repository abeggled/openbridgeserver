#!/usr/bin/env node
// Builds the help site into a throwaway directory and prints, per rendered
// page, the heading ids VitePress actually emitted:
//
//   { "de/dashboard/overview.html": ["dashboard", "dashboard-stats-datapoints", …] }
//
// tools/check_help_contract.py compares the generated help-index.json against
// this. The index is produced by a text scan of the Markdown, and every rule
// that scan needs — fenced code, HTML comments, raw HTML blocks, escaped
// braces, frontmatter, indented headings — is a rule about what VitePress
// renders. Measuring the render instead of reproducing its parser is the only
// way to be sure the two agree.
//
// The build never writes to help_dist/: three integration tests assert that
// directory is absent, and a leftover build would fail them.

import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, readdirSync, readFileSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'

// fileURLToPath, not `.pathname`: a checkout path containing a space stays
// percent-encoded in the URL and the resolved path would not exist.
const REPO_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)))
const HELP_ROOT = join(REPO_ROOT, 'help')
const HEADING_ID_RE = /<h[1-6][^>]*\sid="([^"]+)"/g

const outDir = mkdtempSync(join(tmpdir(), 'obs-help-rendered-'))
try {
  execFileSync('npx', ['--no-install', 'vitepress', 'build', '--outDir', outDir], {
    cwd: HELP_ROOT,
    stdio: ['ignore', 'ignore', 'pipe'],
    encoding: 'utf-8',
  })

  const pages = {}
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (entry.name.endsWith('.html')) {
        const ids = [...readFileSync(full, 'utf-8').matchAll(HEADING_ID_RE)].map((match) => match[1])
        pages[relative(outDir, full).split('\\').join('/')] = ids
      }
    }
  }
  walk(outDir)
  process.stdout.write(JSON.stringify(pages) + '\n')
} finally {
  rmSync(outDir, { recursive: true, force: true })
}
