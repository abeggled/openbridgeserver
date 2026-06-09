# Contributing — obs Visu (Mobile App + pluggable Skin-System)

> Arbeits- und Branch-/PR-Workflow für das Visu-Vorhaben. Gilt **nur** für die Visu-Arbeit
> (Pfade `packages/contract/`, `apps/visu/` im Fork sowie das separate Repo
> `Micsi/obs-visu-skins`). Berührt bestehende openbridgeserver-Konventionen nicht.

## Leitsatz

**Alles im Fork. Upstream (`abeggled/openbridgeserver`) sieht nichts, bis die Visu
weitgehend releasereif ist.** Intern arbeiten wir **issue-basiert** (viele kleine PRs in
einen Integrationsbranch); nach Upstream liefern wir am Ende **wenige, gestapelte PRs**.

## Repos & Rollen

| Repo | Inhalt | Sichtbar upstream? |
|---|---|---|
| `Micsi/openbridgeserver` (Fork) | App (`apps/visu`) + Vertrag (`packages/contract` = `@obs/visu-contract`) | erst beim Release-PR |
| `Micsi/obs-visu-skins` | Skins (`ionic`, `terminal`, …) + Tooling (Konformität, Fixture-Wand) | eigenständig |

Kopplung ausschließlich über das **versionierte** `@obs/visu-contract` (Semver). App und
Skins kennen einander nie direkt.

## Branch-Modell

```
main (Fork, gespiegelt von upstream)
  └── feat/visu-mobile-skins        ← langlebiger Integrationsbranch (verlinkt mit Epic #84)
        ├── feat/visu-c2-schema      ← Topic-Branch je Issue
        ├── feat/visu-co3-store
        └── …
```

1. **Integrationsbranch** `feat/visu-mobile-skins` ist Ziel aller Visu-Arbeit und mit dem
   Epic (#84) als „development branch" verknüpft. Wird **nie** direkt nach upstream gepusht.
2. **Topic-Branch je Issue**, abgezweigt von `feat/visu-mobile-skins`
   (`feat/visu-<id>-<kurz>`). Klein halten — ein Issue, ein Branch.
3. **Fork-interner PR** des Topic-Branches **gegen `feat/visu-mobile-skins`**, mit
   `Closes #<issue>` im PR-Body. Review + CI laufen pro Baustein.
4. Squash-Merge in den Integrationsbranch (saubere, issue-gekoppelte Historie).

## Issues, Sub-Issues, Milestones

- Jede Arbeit hat ein Issue unter dem Epic (Sub-Issue-Verknüpfung ist gesetzt).
- **Reihenfolge** richtet sich nach den Milestones **M1 → M4**:
  - **M1 Fundament:** Contract + Core + Repo-Setup
  - **M2 Ionic-Skin + Host + erste Seite**
  - **M3 Terminal-Skin + Tooling + Fixture-Wand**
  - **M4 Capacitor/PWA + Härtung**
- Abhängigkeiten stehen im Issue-Body. Kritische Kette kurz halten:
  `C1→C2→{C3,C4}→model→datasource→store→host-dispatch→host-actions→Seiten→Capacitor`.
- Pro Milestone-Ende ein **Tag** auf dem Fork als Checkpoint (`visu-m1`, `visu-m2`, …).

## Upstream-Sync (wöchentlich)

```bash
git fetch origin                       # origin = abeggled (upstream)
git switch feat/visu-mobile-skins
git merge origin/main                  # oder rebase; Konflikte früh lösen
```

Konflikte sollten minimal sein (Visu lebt in neuen Pfaden); Reibung v. a. an Root-Configs
(`pnpm-workspace.yaml`, `package.json`, CI). Früh & oft syncen hält den späteren
Upstream-PR mergebar.

## Vertrag während der Entwicklung

- Skins beziehen `@obs/visu-contract` **nicht** über npm, sondern per
  pnpm-Workspace-Link / Git-Dependency (TODO: Mechanismus final festlegen, siehe U2/D5).
- **npm-publish erst zum Release.** Bis dahin ist der Vertrag „internes" Paket.
- Jede Formänderung am Vertrag = Semver-Bump + `CHANGELOG.md`. Bricht ein Skin, wird seine
  Fixture-Wand rot — das ist gewollt.

### Dev-Link (M2)

Während M2 hängen App, Skin und Vertrag über lokale `link:`-Pfade zusammen (kein npm-publish).
Beide Repos kennen einander nicht (Entkopplung, ARCHITECTURE.md §1) — der Link ist reines
Entwicklungs-Tooling, kein Vertragsbestandteil. Die beiden cross-repo Links:

- **App → Ionic-Skin** (in `apps/visu/package.json`):
  `"@obs-visu-skins/ionic": "link:/Volumes/Daten/Projekte/openbridge/obs-visu-skins/packages/skins/ionic"`
- **Ionic-Skin → Vertrag** (in `obs-visu-skins/packages/skins/ionic/package.json`):
  `"@obs/visu-contract": "link:/Volumes/Daten/Projekte/openbridge/openbridgeserver-visu-integrate/packages/contract"`

Auflösen mit `pnpm install` im jeweiligen Repo-Root; danach erreicht die App den Skin und der
Skin den Vertrag über die Symlink-Kette. Verifiziert per Vitest
(`apps/visu/tests/ionic-skin-link.test.ts`): Manifest-Form + `targetsContract`.

> **Bei Release auf publizierte Versionen umstellen:** Die absoluten `link:`-Pfade werden durch
> die veröffentlichten npm-Versionen ersetzt (`@obs/visu-contract`: `^x.y.z`,
> `@obs-visu-skins/ionic`: `^x.y.z`). Absolute Pfade sind maschinengebunden und dürfen nie in
> einen Release-Stand sickern.

## CI-Gates

- **Fork:** `lint`, `typecheck`, `build`, **Vitest** (Pflicht bei GUI-Änderungen),
  Schema-Validierung des Vertrags.
- **Skins-Repo:** `lint`, `typecheck`, `build` **+ Konformitätslauf** — CI bricht bei
  `gap`/`broken` für die Ziel-Vertragsversion. `support.json` wird als Artefakt abgelegt.
- **Definition of Done eines Skins:** die Fixture-Wand ist grün.

## Pre-Push-Hook

Visu-Branches sind **JS-only** (Pfade `packages/contract`, `apps/visu/src`). Das committete
**Backend-`ruff`-Pre-Push-Gate** ist für sie **irrelevant** und scheitert zudem an einem
**vorbestehenden Backend-Fehler** (`knxproj.py` → `get_admin_user`), der nichts mit den
Visu-Diffs zu tun hat.

Deshalb wird der Hook für Visu-Pushes gezielt umgangen:

```bash
git -c core.hooksPath=/dev/null push fork HEAD:feat/visu-mobile-skins
```

- **Nicht** `--no-verify` verwenden — der Hook wird ausschließlich für genau diesen Push
  über `core.hooksPath=/dev/null` deaktiviert, nicht dauerhaft abgeschaltet.
- Der vorbestehende Backend-Fehler (`knxproj.py get_admin_user`) wird **separat** behoben und
  ist kein Visu-Blocker.

## Goldene Regeln (gelten in jedem PR)

1. Kein Datenfork pro Skin — ein Modell, Skins lesen schreibgeschützt.
2. Renderer nach Typ adressiert (`renderers[type]`), kein stiller `switch`-Default.
3. „Nicht unterstützt" ist Pflichtangabe (`unsupported`), kein Vergessen.
4. Der Skin besitzt nie State — der Host mappt Gesten auf kanonische Aktionen.
5. Reihenfolge + Gruppierung sind Layout-Boden; Rollen/Spans additiv & ignorierbar.
6. AA-Kontrast Pflicht, auch an den Tweak-Extremen.
7. Daten als JSON, Verhalten als TS/JS — der Vertrag führt nichts aus.

## Release nach Upstream (am Ende)

Wenn die Visu weitgehend releasereif ist: **wenige, gestapelte PRs** entlang der
Workstreams statt einem Riesen-PR oder 30 Mini-PRs, z. B.:

- **PR-A:** `@obs/visu-contract` + `core`
- **PR-B:** App-Shell + Skin-Host + Ionic-Anbindung
- **PR-C:** Mobile/Capacitor + Härtung

Das Skins-Repo bleibt eigenständig; ob/wie es zu Upstream gehört, ist eine separate
Entscheidung. PR-Identität, Worktree-Hygiene und Commit-Sichtbarkeit folgen den
Upstream-PR-Guardrails des Projekts.

## Don'ts

- Keine PRs/Drafts nach `abeggled`, bevor es releasereif ist.
- Den Integrationsbranch nicht nach upstream pushen.
- Keine State-Mutation in Skins; keine direkten Datenfelder-Schreibzugriffe im Renderer.
- Keine lokalen Workflow-/Planungsartefakte in Upstream-Commits.
- **Kein `git stash` / `git stash pop` im geteilten Klon.** Worktrees teilen sich die Stash-Liste — ein `pop` kann fremde Stashes anderer Worktrees (z. B. `issue-*`) einspielen. Jeder Agent arbeitet ausschließlich in seinem **eigenen Worktree** (`git worktree add … fork/feat/visu-mobile-skins`) und committet nur seine zugewiesenen Pfade; nie `git add -A` über fremde Änderungen.
- Pushes für `feat/visu-*` laufen mit `git -c core.hooksPath=/dev/null push` (JS-only; das Backend-Pre-Push-Gate ist irrelevant) — **nicht** `--no-verify`.
