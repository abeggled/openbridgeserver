# AGENTS/CLAUDE Alias Note

`AGENTS.md` is the canonical agent-instructions file in this repository.
`CLAUDE.md` is a symlink to this same file for tool compatibility.
You only need to read one of them; reading both is redundant.

# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

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

```bash
# Run the server
tools/with-venv python -m obs

# Run all tests
tools/with-venv pytest tests/

# Run a single test file
tools/with-venv pytest tests/unit/test_converter.py

# Run a specific test
tools/with-venv pytest tests/unit/test_converter.py::test_float_to_int

# Run only adapter + unit tests (no Docker needed)
tools/with-venv pytest tests/adapters/ tests/unit/

# Run contract tests — verify external library API surfaces (no Docker needed)
tools/with-venv pytest tests/contracts/

# Run integration tests (requires Docker for Mosquitto)
tools/with-venv pytest tests/integration/

# Run with coverage report
tools/with-venv pytest tests/ --cov=obs --cov-report=term-missing

# Lint (same checks as CI)
tools/with-venv ./tools/lint.sh --check

# Format + autofix
tools/with-venv ./tools/lint.sh --fix

# Docker Compose (full stack)
docker compose up -d

# Docker Compose (Mosquitto only — for local dev outside Docker)
docker compose up -d mosquitto

# Build release artifacts locally (only requires Docker)
./tools/build-local.sh docker    # Docker image (via docker compose build obs + version stamp)
./tools/build-local.sh lxc       # Proxmox LXC template (.tar.zst); rootfs cached in ~/.cache/obs-lxc-builder/
./tools/build-local.sh bundle    # app bundle only, no rootfs (fast)
./tools/build-local.sh all       # docker + lxc

# Admin GUI dev server (proxies /api to localhost:8080)
cd gui && npm run dev

# Build the integrated help site for local testing (help_dist/ is gitignored
# and NOT built by `python -m obs` — without this, the Admin-GUI's help
# drawer shows its generic "unavailable" fallback for every topic)
cd help && npm run build
```

## Pre-Push Gate (verbindlich)

CI im PR-Workflow baut das Frontend **nicht** — `npm run build` läuft erst beim Release-Tag.
Ein Production-Build validiert die Frontend-Abhängigkeiten (Module-Resolution, Import-Pfade,
Typen) Ende-zu-Ende — Schwächen, die Vitest-Mocks verdecken können, fallen erst hier auf. Vor
**jedem** Push, der Frontend-Code berührt, alle Stufen lokal grün laufen lassen:

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

Wenn rot: nicht pushen, sondern fixen. Gilt für Haupt-Sessions **und** für Subagents — bitte
explizit in den Subagent-Prompt schreiben, weil deren Kontext leer startet.

Zusätzlich für i18n-Änderungen (Admin GUI + Visu) gilt ein harter Diff-Gate:

```bash
# Diff-basierter i18n Hard-Gate (Hardcoded user-facing strings + locale parity)
./tools/check-i18n-hardcoded-strings.sh
```

Für GUI-Änderungen gilt zusätzlich ein weicher Coverage-Nachzieh-Hinweis:

```bash
node tools/gui-coverage-summary.mjs --changed-only --threshold=70
```

Wenn geänderte Dateien unter dem Schwellwert liegen, im Abschlussbericht konkret nennen
und möglichst passende Vitests ergänzen. Der Hinweis ist bewusst kein lokaler Hard-Fail;
`npm run test` und `npm run test:coverage` bleiben dagegen verpflichtende Gates und
brechen bei Fehlern ab.

Optional als lokaler Push-Hook aktivieren:

```bash
git config core.hooksPath .githooks
```

Danach läuft der i18n-Gate automatisch vor jedem `git push`.

## Test Coverage Gate (verbindlich)

**Jede neue Zeile Code muss durch Tests abgedeckt sein — und jeder neue Zweig einer Bedingung
ebenfalls.** Codecov prüft die *Patch Coverage* auf zwei Ebenen, die beide grün sein müssen:

1. **Line Coverage** — wurde jede im Diff hinzugefügte Zeile mindestens einmal ausgeführt?
2. **Branch Coverage** — wurde bei jeder Bedingung *auf* einer geänderten Zeile jeder mögliche
   Ausgang mindestens einmal durchlaufen?

Das ist strenger als nur die Gesamtabdeckung zu halten: eine neue Funktion, die bestehende Zeilen
nicht senkt, aber selbst ungetestet bleibt, fällt durch dieses Gate — und genauso eine Zeile, die
zwar *ausgeführt* wurde, deren `if`/`else`, `try`/`finally`, `||`/`??`/Ternary oder Mehrfach-Guard
(`a || b || c`) aber nur auf einer Seite getestet wurde. **Codecov markiert diesen zweiten Fall
als „partial" und lässt das Gate genauso fehlschlagen wie eine komplett fehlende Zeile — obwohl
lokale Line-Coverage-Reports (`--cov-report=term-missing`, Vitest-Tabellenausgabe) das oft nicht
zeigen, weil sie primär Line- statt Branch-Daten hervorheben.** Ein lokal grüner Lauf ist daher
keine Garantie für einen grünen `codecov/patch`-Check — insbesondere wenn bestehender Code neu in
`try`/`if` eingepackt wird (das re-indentiert die Zeilen, wodurch sie im Diff als „neu" zählen und
ihre ggf. schon vorher unvollständige Branch-Abdeckung erstmals gegen das Gate zählt).

- **Neuer Code → neue Tests im selben Commit.** Jede neue API-Route, jeder neue Adapter, jede
  neue Hilfsfunktion braucht zugehörige Tests (Unit oder Integration), die genau die neuen Zeilen
  ausführen — und bei jeder neuen/geänderten Bedingung Tests für **beide Seiten** (true/false,
  vorhanden/fehlend, Guard greift/greift nicht).
- **Refactorings** dürfen weder die Gesamtabdeckung noch die Patch Coverage (Line **und** Branch)
  senken. Das Einpacken von bestehendem Code in ein neues `try`/`if`/`??` zählt als Refactoring in
  diesem Sinne — vorher prüfen, ob die betroffenen Zeilen schon alle Zweige abgedeckt hatten.
- **Vor jedem Push mit Backend-Änderungen** Coverage prüfen:

```bash
# Gesamtabdeckung (muss ≥ Baseline bleiben):
tools/with-venv pytest tests/unit tests/adapters tests/contracts --cov=obs --cov-report=term-missing
# mit Docker auch:
tools/with-venv pytest tests/integration --cov=obs --cov-append --cov-report=term-missing
```

Wenn neue Zeilen im Report unter „Missing" auftauchen: Tests ergänzen, nicht pushen.

- **Vor jedem Push mit GUI-Änderungen** ebenso `(cd gui && npm run test:coverage)` laufen lassen.
  Für Branch-Detail auf einzelne Zeilen (nicht nur die aggregierte %-Spalte) die generierte
  `gui/coverage/lcov.info` auswerten — `BRDA:<line>,<block>,<branch>,<hits>`-Zeilen mit `hits == 0`
  markieren einen ungetesteten Zweig genau dieser Zeile:

```bash
awk '/SF:.*<Dateipfad>$/{f=1} f && /^BRDA:/{print} f && /end_of_record/{f=0}' gui/coverage/lcov.info \
  | awk -F, '$4 == 0'
```

- **Bei einem bereits offenen PR**, wenn unklar ist, ob eine Änderung das Gate reißt: den
  tatsächlichen `codecov/patch`-Check auf dem PR prüfen (`gh pr checks <nr>`), nicht nur lokale
  Reports vertrauen — siehe oben, lokal und Codecov können bei Branch-Partials auseinanderlaufen.

Gilt für Haupt-Sessions **und** für Subagents — bitte explizit in den Subagent-Prompt schreiben.

## Detailed guidance routing (MUST)

The detailed, task-specific instructions live in `docs/AGENT_REFERENCE.md`. Read the relevant
named sections there before acting; do not assume the root instruction budget contains them:

- Before configuring a development environment or linked worktree, read the applicable parts of
  `Local Development Setup`. The command, pre-push, and coverage rules remain in this root file.
- Before changing either frontend or any user-facing text, read `GUI architecture` and the complete
  `Internationalisation (i18n)` section, including its hard gate and Weblate source-language rule.
- Before changing backend startup, data flow, adapters, configuration, authentication, tests, or
  dependencies, read the applicable parts of `Architecture`.
- Before adding or changing a Logic function block, read `docs/architecture/logic-nodes.md` — it
  defines the node/registry contract, the allowed dependency direction, and the procedure for
  adding a new block. Automated guardrail tests enforce these rules.
- Before changing workflows, versioning, images, LXC packaging, runtime scripts, or release notes,
  read the applicable parts of `Release & CI`.

When more than one category applies, read every applicable section. These referenced instructions
are mandatory and have the same authority as this file. If a referenced instruction conflicts with
this root file, this root file wins.

## Code Review Rules

### Consolidated review

- Review the complete effective pull-request diff at one frozen public GitHub PR HEAD.
- Limit reportable findings to defects introduced or materially worsened by that effective diff.
  Inspect unchanged context only to understand the changed code's behavior; do not report unrelated
  pre-existing defects from elsewhere in a touched or renamed file.
- A case-only rename, file move, mode change, symlink-target update, formatting-only change, or line
  movement does not make unchanged file contents part of the review scope.
- Do not post findings incrementally. Run repeated internal review passes, combine and deduplicate
  their results, and post one consolidated review.
- Continue until two consecutive complete passes on that same HEAD produce no new findings. If
  that cannot be completed, report review coverage as partial and list every deferred surface.
- Publish one partial-coverage notice listing every deferred surface and its exact blocker. In a
  `findings`-only transport, emit one `[P3] [PARTIAL] Review coverage incomplete` transport envelope
  instead of repeating the coverage metadata in individual items. State explicitly that this envelope
  is neither a confirmed finding nor a severity.

### Reproduction preflight

- Before investigating candidates, run one preflight for each relevant test surface and verify that
  its documented runner and shared dependencies can start. Record the commands, exit statuses, and
  logs in the review state.
- A shared preflight failure defers that complete surface. Report it once as partial coverage and do
  not publish the untested hypotheses from that surface as individual blocked candidates.
- Classify a candidate as `blocked` only when its shared surface preflight passed and a prerequisite
  unique to that candidate, such as credentials, hardware, a service, data, or permission, remains
  unavailable.

### Reproduction evidence

- The consolidated review has two reportable classes: `Confirmed findings` and `Blocked candidates`.
  Put an item under confirmed findings only when it is `reproduced`, has a demonstrated reachable
  execution or use path, and meets the bar for an actionable defect; only confirmed findings carry
  a defect severity.
- A meaningful reproducer must exercise an actual supported entry point, caller, request or API route,
  event, command, configuration consumer, or documented workflow through the affected code to the
  observed failure. A suspicious source pattern, isolated function invocation that production cannot
  reach, or script that merely asserts a hypothetical condition is not evidence of a defect.
- Use `not_reproduced` only when every required prerequisite was available, an adequate reproducer
  ran to completion and the claimed behavior did not occur, or no reachable path from a supported
  entry point to the claimed behavior could be demonstrated. Exclude such candidates from the
  published review and retain them only in internal deduplication state for the reviewed HEAD.
- Keep every candidate-specific `blocked` item visible in the consolidated review, but do not present
  it as confirmed or count it as a finding. Failure to identify a reachable path is `not_reproduced`,
  not `blocked`.
- When the review transport supports a summary body, publish confirmed findings and blocked candidates
  in separate sections. When it accepts only a `findings` array, use that array as a transport envelope
  for blocked candidates: place `[BLOCKED]` immediately after the transport's required priority prefix
  (for example, `[P3] [BLOCKED] ...`), use the lowest supported priority only as a schema placeholder,
  and state explicitly that the entry is not a confirmed finding and the priority is not a severity.
- Every published finding or blocked candidate must include the complete executable reproduction code
  inline or link to a publicly accessible committed artifact. A temporary filename or prose description
  of code is not executable evidence. Also include the exact command, expected behavior, observed
  application or supported-workflow behavior, exit status, validation logs, and status.
- Dependency installation, test discovery, and test-runner startup failures are preflight evidence,
  not observed defect behavior. Never use such a failure to claim that a candidate reproduced.
- Every confirmed finding must also demonstrate the causal link to the effective diff. When practical,
  run the same reproducer against the captured base and head and show that the failure is absent at the
  base and present at the head; otherwise explain why that comparison cannot run and prove the causal
  link another way.
- A blocked candidate must include the attempted reproducer and exact blocker. Never present it as
  confirmed without successful reproduction.
- Every security item must additionally state the attacker capabilities, crossed trust boundary,
  and affected asset. A `reproduced` security finding must include executable exploit or proof-of-
  concept code that demonstrates the claimed impact. A blocked security candidate must instead
  include the attempted proof of concept, missing prerequisite, and potential impact; label it
  unconfirmed and do not claim demonstrated exploitability.

### Re-review discipline

- Bind every GitHub PR review to the publicly fetchable base and `refs/pull/<number>/head` SHAs
  captured when the review starts. For a non-PR `--commit` or `--base` review, bind instead to the
  explicitly requested or resolved comparison commit and reviewed commit. If uncommitted changes or
  a synthetic commit cannot be represented by those commits, report that separately and include the
  complete required patch with every reproduction command.
- For every commit-backed review, compute the effective-diff fingerprint from the exact NUL-delimited
  raw tree delta below, with no further normalization. For a PR, set `BASE_SHA` and `HEAD_SHA` to the
  captured public GitHub SHAs. For a non-PR commit/base review, set them to the resolved comparison and
  reviewed commit SHAs. Always retain the review mode, both inputs, `git --version`, and the SHA-256
  output in internal review state for the frozen target:

  ```bash
  set -euo pipefail
  git cat-file -e "${BASE_SHA}^{commit}"
  git cat-file -e "${HEAD_SHA}^{commit}"
  MERGE_BASE=$(git merge-base "$BASE_SHA" "$HEAD_SHA")
  git diff-tree --no-commit-id --raw -r -z --no-abbrev --no-renames "$MERGE_BASE" "$HEAD_SHA" \
    | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
  ```

- Publish the fingerprint metadata in the consolidated summary when that surface exists. For a
  `findings`-only transport, include it in every emitted confirmed finding or blocked-candidate body.
  If the array is empty, emit no synthetic metadata finding; keep the metadata only in internal state.
- Re-review the complete effective pull-request diff, but deduplicate against existing findings by path,
  violated invariant, affected data or control flow, and observed behavior.
- Carry earlier findings and their dispositions into a separate prior-finding state. Keep the
  existing thread authoritative; do not post a duplicate or count it as a new current finding.
- Honor `will not fix`, `later`, and `follow-up` dispositions. Reopen the existing thread, without
  posting a duplicate, when a claimed fix is incomplete or ineffective and the defect still
  reproduces, when a fix regressed, or when materially new evidence changes the invariant, affected
  flow, or observed behavior.
- A base-branch merge or line-number change does not make an existing finding new.
