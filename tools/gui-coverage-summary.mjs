#!/usr/bin/env node
import { existsSync, readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { relative, resolve, sep } from 'node:path'

const repoRoot = resolve(new URL('..', import.meta.url).pathname)
const guiRoot = resolve(repoRoot, 'gui')
const summaryPath = resolve(guiRoot, 'coverage/coverage-summary.json')

const args = new Set(process.argv.slice(2))
const thresholdArg = process.argv.find((arg) => arg.startsWith('--threshold='))
const threshold = Number(thresholdArg?.split('=')[1] ?? 70)
const changedOnly = args.has('--changed-only')

function gitOutput(command) {
  return execSync(command, { cwd: repoRoot, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
}

function changedGuiSourceFiles() {
  let base = ''
  try {
    base = gitOutput('git merge-base HEAD @{upstream}')
  } catch {
    try {
      base = gitOutput('git merge-base HEAD origin/main')
    } catch {
      base = ''
    }
  }
  const range = base ? `${base}..HEAD` : 'HEAD~1..HEAD'
  try {
    return new Set(
      gitOutput(`git diff --name-only --diff-filter=ACMR ${range}`)
        .split('\n')
        .filter((path) => /^gui\/src\/.*\.(js|vue)$/.test(path)),
    )
  } catch {
    return new Set()
  }
}

function normalizeCoveragePath(path) {
  const absolute = resolve(guiRoot, path)
  const repoRelative = relative(repoRoot, absolute).split(sep).join('/')
  return repoRelative.startsWith('gui/') ? repoRelative : path.split(sep).join('/')
}

if (!existsSync(summaryPath)) {
  console.log('[coverage hint] missing gui/coverage/coverage-summary.json; run npm run test:coverage in gui first.')
  process.exit(0)
}

const summary = JSON.parse(readFileSync(summaryPath, 'utf8'))
const changedFiles = changedOnly ? changedGuiSourceFiles() : null
const rows = Object.entries(summary)
  .filter(([path]) => path !== 'total')
  .map(([path, metrics]) => ({
    path: normalizeCoveragePath(path),
    lines: Number(metrics.lines?.pct ?? 0),
    branches: Number(metrics.branches?.pct ?? 0),
  }))
  .filter((row) => !changedFiles || changedFiles.has(row.path))
  .filter((row) => row.lines < threshold)
  .sort((a, b) => a.lines - b.lines || a.path.localeCompare(b.path))

if (!rows.length) {
  const scope = changedOnly ? 'changed GUI source files' : 'GUI source files'
  console.log(`[coverage hint] no ${scope} below ${threshold}% line coverage.`)
  process.exit(0)
}

console.log(`[coverage hint] ${rows.length} GUI source file(s) below ${threshold}% line coverage:`)
for (const row of rows.slice(0, 20)) {
  console.log(`  - ${row.path}: ${row.lines}% lines, ${row.branches}% branches`)
}
if (rows.length > 20) {
  console.log(`  ... ${rows.length - 20} more`)
}
