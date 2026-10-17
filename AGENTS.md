# AGENTS.md

`CLAUDE.md` is a symlink to this file, so reading one of them is enough.

## Project Overview

**open bridge server** is an open-source multiprotocol building automation server (MIT-licensed
replacement for the proprietary Timberwolf Server). It bridges KNX, Modbus RTU/TCP, 1-Wire,
MQTT, SNMP, Home Assistant, ioBroker, presence simulation, and scheduling into a unified system
with a FastAPI REST/WebSocket API and Vue-based admin and visualisation frontends.

## Repository Layout

Two top-level directories have distinct, non-overlapping purposes — keep them that way:

| Directory | Audience | What belongs here |
|---|---|---|
| `tools/` | Developers, CI, release pipeline | Dev tooling only. Nothing here is installed on a running OBS host. |
| `scripts/` | Running OBS host | Deployed runtime scripts installed onto a production or LXC host. |

If a file is only needed to build, test, or check the project, it goes in `tools/`. If it ends up
on the host after installation, it goes in `scripts/`.

## Common Commands

**`tools/with-venv` prefixes every Python command.** It resolves `OBS_VENV`, then this worktree's
`.venv`, then the main worktree's `.venv`, and fails hard rather than installing anything. Plain
`python`/`pytest` may hit a different interpreter than CI does.

```bash
tools/with-venv python -m obs                  # run the server
tools/with-venv pytest tests/                   # ...and any other pytest invocation
tools/with-venv ./tools/lint.sh --check         # the lint CI runs; --fix formats and autofixes
```

`tests/adapters`, `tests/unit` and `tests/contracts` need no Docker; `tests/integration` starts
Mosquitto in a container. For local dev outside Docker, `docker compose up -d mosquitto` brings up
that broker alone. The Admin GUI dev server (`cd gui && npm run dev`) proxies `/api` to
`localhost:8080`, so it needs a running OBS beside it.

Two builds are not discoverable from `package.json` or `--help`:

```bash
./tools/build-local.sh docker|lxc|bundle|all   # release artifacts; needs only Docker. `docker` runs
                                               # `docker compose build obs` plus a version stamp,
                                               # `lxc` caches its rootfs in ~/.cache/obs-lxc-builder/,
                                               # `bundle` skips the rootfs and is the fast one.
cd help && npm run build                       # the integrated help site. `help_dist/` is gitignored
                                               # and `python -m obs` does NOT build it — without this
                                               # every help topic shows the "unavailable" fallback.
```

## Pre-Push Gate (verbindlich)

Beide Gates dieser Datei existieren gegen **Grün-Fallen**: ein Lauf ist grün, ohne die Eigenschaft
geprüft zu haben, auf die es ankommt.

Die erste: CI im PR-Workflow baut das Frontend **nicht**, `npm run build` läuft erst beim
Release-Tag. Erst der Production-Build prüft Module-Resolution, Import-Pfade und Typen gegen die
echten Abhängigkeiten; Vitest-Mocks können genau diese Schwächen verdecken. Vor **jedem** Push,
der Frontend-Code berührt, alle Stufen lokal grün laufen lassen:

```bash
tools/with-venv ./tools/lint.sh --check
(
  cd gui
  npm run build          # validiert reale Abhängigkeiten/Imports, was Vitest nicht tut
  npm run test
  npm run test:coverage
)
# bei Backend-Änderungen zusätzlich:
tools/with-venv pytest tests/unit tests/adapters tests/contracts  # ohne Docker
tools/with-venv pytest tests/integration                          # mit Docker
```

Wenn rot: nicht pushen, sondern fixen.

**Gilt für Haupt-Sessions und für Subagents.** Deren Kontext startet leer, also gehören diese
Gates und die Coverage-Regeln weiter unten ausdrücklich in den Subagent-Prompt.

Zusätzlich für i18n-Änderungen (Admin GUI + Visu) gilt ein harter Diff-Gate:

```bash
# Diff-basierter i18n Hard-Gate (Hardcoded user-facing strings + locale parity)
./tools/check-i18n-hardcoded-strings.sh
```

Für neue Admin-Routen, neue Visu-Widget-Typen, neue Logik-Bausteine und für Änderungen an
`help/` gilt der Doku-Gate (#1183) — jede doku-pflichtige Fläche braucht eine auflösende
`help_id`:

```bash
# Hilfe-Abdeckung der UI-Flächen (Routen, Widgets, Logik-Bausteine, Skins) + auflösbare help_ids
tools/with-venv python tools/check_help_contract.py
```

Er parst beide Frontends (`@babel/parser`, `@vue/compiler-sfc`, in `gui/` deklariert) und baut die
Hilfeseite einmal, um den generierten Index gegen die wirklich gerenderten Anker zu prüfen; er
braucht deshalb `gui/node_modules` und `help/node_modules`. Ohne diesen Abgleich läuft er mit
`--skip-render-check`.

Optional als lokaler Push-Hook aktivieren:

```bash
git config core.hooksPath .githooks
```

Danach läuft der i18n-Gate automatisch vor jedem `git push`.

## Test Coverage Gate (verbindlich)

**Jede neue Zeile braucht einen Test, und jeder neue Zweig einer Bedingung ebenfalls.** Codecov
prüft die *Patch Coverage* auf beiden Ebenen, und beide müssen grün sein: wurde jede hinzugefügte
Zeile ausgeführt (Line), und wurde bei jeder Bedingung *auf* einer geänderten Zeile jeder Ausgang
durchlaufen (Branch)?

Das ist strenger als die Gesamtabdeckung zu halten. Eine neue Funktion, die nichts senkt, aber
selbst ungetestet bleibt, fällt durch — und ebenso eine Zeile, die zwar lief, deren `if`/`else`,
`try`/`finally`, `||`/`??`/Ternary oder Mehrfach-Guard (`a || b || c`) aber nur auf einer Seite
geprüft wurde. Codecov markiert das als „partial" und lässt das Gate genauso fallen wie eine
fehlende Zeile.

**Die Grün-Falle dieses Gates:** lokale Reports (`--cov-report=term-missing`, Vitest-Tabelle)
heben Line- statt Branch-Daten hervor und zeigen solche Partials oft nicht. Ein lokal grüner Lauf
ist deshalb keine Zusage für einen grünen `codecov/patch`. Besonders heimtückisch, wenn
bestehender Code neu in `try`/`if` eingepackt wird: das re-indentiert die Zeilen, sie zählen im
Diff als neu, und ihre womöglich schon vorher unvollständige Branch-Abdeckung zählt erstmals
gegen das Gate.

- **Neuer Code → neue Tests im selben Commit**, die genau die neuen Zeilen ausführen, und bei
  jeder neuen oder geänderten Bedingung **beide Seiten** (true/false, vorhanden/fehlend, Guard
  greift/greift nicht).
- **Refactorings** senken weder Gesamt- noch Patch Coverage. Bestehenden Code in ein neues
  `try`/`if`/`??` einzupacken zählt hier als Refactoring: vorher prüfen, ob die Zeilen schon alle
  Zweige abgedeckt hatten.
- **Bei einem offenen PR** entscheidet der echte `codecov/patch`-Check (`gh pr checks <nr>`),
  nicht der lokale Report.

```bash
# Backend, vor dem Push:
tools/with-venv pytest tests/unit tests/adapters tests/contracts --cov=obs --cov-report=term-missing
tools/with-venv pytest tests/integration --cov=obs --cov-append --cov-report=term-missing   # mit Docker

# GUI, vor dem Push:
(cd gui && npm run test:coverage)
node tools/gui-coverage-summary.mjs --changed-only --threshold=70   # weicher Hinweis, kein Hard-Fail

# Branch-Detail je Zeile aus dem lcov-Report (hits == 0 = ungeprüfter Zweig dieser Zeile):
awk '/SF:.*<Dateipfad>$/{f=1} f && /^BRDA:/{print} f && /end_of_record/{f=0}' gui/coverage/lcov.info \
  | awk -F, '$4 == 0'
```

Tauchen neue Zeilen unter „Missing" auf: Tests ergänzen statt pushen. Liegen geänderte
GUI-Dateien unter dem Schwellwert, im Abschlussbericht konkret nennen und möglichst Vitests
nachziehen — `npm run test` und `npm run test:coverage` bleiben dagegen harte Gates.

## Detailed guidance routing (MUST)

The detailed, task-specific instructions live in `docs/AGENT_REFERENCE.md`. This file carries the
commands, gates and review rules; everything else is there. Read the relevant named sections before
acting:

- Before configuring a development environment or linked worktree, read the applicable parts of
  `Local Development Setup`. The command, pre-push, and coverage rules remain in this root file.
- Before changing either frontend or any user-facing text, read `GUI architecture` and the complete
  `Internationalisation (i18n)` section, including its hard gate and Weblate source-language rule.
- Before changing backend startup, data flow, adapters, configuration, authentication, tests, or
  dependencies, read the applicable parts of `Architecture`.
- Before adding an Admin route, a Visu widget type, a Logic function block, or help content,
  read `Help site translations`
  and `Help contract gate` — CI fails when a documentation-required surface has no resolvable
  `help_id`.
- Before adding or changing a Logic function block, read `docs/architecture/logic-nodes.md` — it
  defines the node/registry contract, the allowed dependency direction, and the procedure for
  adding a new block. Automated guardrail tests enforce these rules.
- Before changing workflows, versioning, images, LXC packaging, runtime scripts, or release notes,
  read the applicable parts of `Release & CI`.

When more than one category applies, read every applicable section. These referenced instructions
are mandatory and have the same authority as this file. If a referenced instruction conflicts with
this root file, this root file wins.

## Code Review Rules

These apply to every code review in this repo, human-run or automated. Four terms carry them and
are used as terms throughout: **reachable path**, **preflight**, **partial coverage**, and the three
candidate states `reproduced` / `blocked` / `not_reproduced`.

A **reachable path** means an actual supported entry point — a caller, request, API route, event,
command, configuration consumer or documented workflow — exercised through the affected code to the
observed failure. A suspicious source pattern, an isolated function invocation that production
cannot reach, or a script that merely asserts a hypothetical condition is not evidence of a defect.

### Scope and consolidation

- Review the complete effective diff at **one frozen HEAD** (for a PR, the public
  `refs/pull/<number>/head`).
- Report only defects that diff introduced or materially worsened. Inspect unchanged context only
  to understand the changed code's behavior; do not report unrelated pre-existing defects from
  elsewhere in a touched or renamed file.
- A case-only rename, file move, mode change, symlink-target update, formatting-only change or line
  movement pulls nothing unchanged into scope.
- **Post once.** Run repeated internal passes, combine and deduplicate, publish one consolidated
  review — never incrementally. Continue until two consecutive complete passes at that HEAD produce
  no new findings. **If that cannot be completed, report coverage as partial and list every
  deferred surface.**

### Preflight before investigating

- Run one **preflight** per relevant test surface first and verify that its documented runner and
  shared dependencies actually start. Record the commands, exit statuses and logs.
- A **shared** preflight failure defers that whole surface: report it once as partial coverage, and
  do not publish the untested hypotheses from it as individual candidates.
- Dependency installation, test discovery and runner startup failures are preflight evidence, never
  observed defect behaviour. They can never support a claim that something reproduced.

### The three states

| State | Earned when | Published as |
|---|---|---|
| `reproduced` | A reproducer exercised a **reachable path** to the observed failure, and it meets the bar for an actionable defect | A **confirmed finding** — the only class that carries a severity |
| `blocked` | The shared surface preflight **passed**, but a prerequisite unique to this candidate (credentials, hardware, a service, data, permission) is unavailable | A **blocked candidate** — visible, but not confirmed and not counted as a finding |
| `not_reproduced` | Every prerequisite was available and an adequate reproducer ran without the claimed behaviour occurring — **or** no reachable path could be demonstrated | Nothing. Excluded from the published review, kept only in internal deduplication state |

Failing to find a reachable path is `not_reproduced`, not `blocked`.

### Evidence each published item carries

- The **complete executable** reproduction code, inline or linked as a publicly accessible committed
  artifact. A temporary filename or a prose description of code is not executable evidence.
- The exact command, the expected behaviour, the observed application or supported-workflow
  behaviour, the exit status, the validation logs and the status.
- For a confirmed finding, the **causal link to the diff**: where practical, run the same reproducer
  against base and head and show the failure absent at base and present at head; otherwise explain
  why that comparison cannot run and establish causality another way.
- For a blocked candidate, the attempted reproducer and the exact blocker.
- For any security item, the attacker capabilities, the crossed trust boundary and the affected
  asset. A `reproduced` security finding carries executable exploit or proof-of-concept code
  demonstrating the claimed impact; a blocked one carries the attempted proof of concept, the
  missing prerequisite and the potential impact, labelled unconfirmed.

### Transport envelopes

Whenever coverage is partial, publish **one** partial-coverage notice listing every deferred
surface and its exact blocker — in whichever transport is in use, not only in the one below.

Where the transport offers a summary body, publish confirmed findings and blocked candidates in
separate sections, and put the fingerprint metadata (below) in the summary.

Where it accepts only a `findings` array, that array doubles as a transport envelope. Then:

- the partial-coverage notice becomes one `[P3] [PARTIAL] Review coverage incomplete` entry, instead
  of repeating coverage metadata per item;
- a blocked candidate carries `[BLOCKED]` immediately after the required priority prefix;
- the lowest supported priority serves only as a schema placeholder, and such an entry states
  explicitly that it is **neither a confirmed finding nor a severity**;
- fingerprint metadata goes in every emitted confirmed finding and blocked candidate. If the array
  is empty, emit no synthetic metadata entry — keep it in internal state.

### Re-review discipline

Bind every review to the commits captured when it starts: for a PR the publicly fetchable base and
`refs/pull/<number>/head` SHAs, for a `--commit`/`--base` review the explicitly requested or
resolved comparison commit and reviewed commit. If uncommitted or synthetic changes cannot be
represented by those commits, report that separately and include the complete required patch with
every reproduction command.

For every commit-backed review, compute the effective-diff fingerprint from exactly this
NUL-delimited raw tree delta, with no further normalization. Set `BASE_SHA` and `HEAD_SHA` to the
captured public SHAs for a PR, or to the resolved comparison and reviewed commits for a
`--commit`/`--base` review. Always retain the review mode, both inputs, `git --version` and the
SHA-256 output in internal state for the frozen target:

```bash
set -euo pipefail
git cat-file -e "${BASE_SHA}^{commit}"
git cat-file -e "${HEAD_SHA}^{commit}"
MERGE_BASE=$(git merge-base "$BASE_SHA" "$HEAD_SHA")
git diff-tree --no-commit-id --raw -r -z --no-abbrev --no-renames "$MERGE_BASE" "$HEAD_SHA" \
  | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
```

On a re-review, cover the complete effective diff again, but deduplicate against existing findings
by path, violated invariant, affected data or control flow, and observed behaviour. Carry earlier
findings and their dispositions in separate prior-finding state: keep the existing thread
authoritative, do not post a duplicate, and do not count it as a new current finding. Honour
`will not fix`, `later` and `follow-up`.

Reopen an existing thread — without posting a duplicate — when a claimed fix is incomplete or
ineffective and the defect still reproduces, when a fix regressed, or when materially new evidence
changes the invariant, the affected flow or the observed behaviour. A base-branch merge or a
line-number change does not make an existing finding new.
