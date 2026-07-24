# Release Policy

## 1. Branch-Modell

| Branch | Zweck | Erlaubte Änderungen |
|---|---|---|
| `main` | Integrations-Branch für laufende Entwicklung; Quelle für `.0`-Releases | Features, Bug-Fixes, alle Typen |
| `YYYY.M` (z. B. `2026.7`) | Bugfix-Branch für ein laufendes Release; nur bei Bedarf anlegen | Bug-Fixes, Security-Fixes, Release-Engineering, Doku |

- `YYYY.M.0`-Tags (und ihre RCs) werden **immer aus `main`** erstellt.
- Bugfix-Branches (`YYYY.M`) werden **nur auf Bedarf** angelegt — wenn nach dem `.0`-Release ein
  Patch-Release notwendig ist. Sie werden aus dem `.0`-Tag herausgeschnitten.
- `YYYY.M.X`-Tags (PATCH > 0) dürfen **nur aus dem zugehörigen `YYYY.M`-Branch** erstellt werden.
- Neue Features auf `main` nach dem `.0`-Tag gehören standardmäßig zum **Folge-Release**.
- In `YYYY.M`-Branches sind keine neuen Features erlaubt — nur Stabilisierung.
- `main` und alle aktiven `YYYY.M`-Branches sind durch Branch Protection gesichert
  (siehe `.github/BRANCH_PROTECTION_CHECKLIST.md`).

## 2. Automatische Versionsvorbereitung nach `.0`-Release

Sobald ein stabiler `.0`-Tag gesetzt ist (kein RC), erstellt `release.yml` automatisch einen PR
auf `main`, der einen leeren Abschnitt `## YYYY.(M+1).0` in `RELEASENOTES.md` einfügt.

Dieser PR muss gereviewed und gemergd werden, bevor der erste Feature-PR des nächsten Release
einläuft. Der Workflow kann bei Bedarf angepasst werden (z. B. Zeitpunkt, PR-Inhalt).

## 3. Release-Zuordnung von Änderungen

- Alles, was **vor** dem `.0`-Tag in `main` gemergt ist, ist Kandidat für das aktuelle Release.
- Alles, was **nach** dem `.0`-Tag in `main` gemergt wird, gehört standardmäßig zum Folge-Release.
- Eine Aufnahme in ein laufendes Release erfolgt nur bewusst via Backport/Cherry-pick auf den
  `YYYY.M`-Branch (→ Abschnitt 5).
- Für frühe Merges in `main`, die erst im Folge-Release aktiviert werden sollen:
  Feature Flags verwenden.

## 4. Milestone-Pflicht (Konvention)

- Jede PR soll einem Milestone zugeordnet sein (z. B. `2026.7` oder `2026.8`).
- Kein blockierender CI-Gate — bei einem kleinen Team ist Konvention zuverlässiger als Automation.
- Milestone-Zuordnung ist Reviewpflicht: PR ohne Milestone wird nicht gemergt.

## 5. Tag-Vertrag (CI-enforced)

Release-Tags folgen CalVer: `YYYY.M.PATCH` für Stable-Releases, `YYYY.M.PATCH-RC<N>` für
Release-Kandidaten. Tags außerhalb dieses Formats lösen keinen offiziellen Release-Workflow aus
(gate in `release.yml`).

Herkunfts-Gate (CI-enforced in `release.yml`):

- `YYYY.M.0` und `YYYY.M.0-RC<N>` → Tag-Commit muss auf `main` liegen.
- `YYYY.M.X` (X > 0) und deren RCs → Tag-Commit muss auf `YYYY.M`-Branch liegen.

Tags auf Feature-Branches erzeugen keinen offiziellen Release.

## 6. Backport-Regeln

Backports in `YYYY.M` sind explizite Ausnahmen und brauchen:

1. Label `backport-YYYY.M` (z. B. `backport-2026.7`)
2. Maintainer-Freigabe (mindestens 1 Review)
3. Cherry-pick mit Verweis auf Ursprungs-PR oder Commit-SHA im PR-Body
4. Grüne Required Checks auf dem Zielbranch

Kein Direkt-Push auf `YYYY.M` — immer über PR.

## 7. Fix-Reihenfolge bei aktivem Bugfix-Release

Betrifft ein Fehler das aktuelle Release:

1. Fix zuerst auf `YYYY.M` umsetzen und mergen.
2. Danach denselben Fix per Cherry-pick oder Forward-Merge nach `main` übertragen.
3. Sicherstellen, dass beide Branches danach konsistent sind.

Kein „fix direkt auf main und dann hoffen" — Divergenz zwischen `main` und dem Bugfix-Branch
führt zu Chaos beim nächsten Forward-Merge.

## 8. Kanäle (Phase 2, #945)

Docker- und LXC-Artefakte werden zusätzlich zum Tag-Vertrag (§5) über kanalbasierte
Manifeste im separaten Repo `abeggled/openbridgeserver-ops` ausgeliefert
(`canary → staging → stable`). `canary` wird nach jedem erfolgreichen Release-Tag-Push
automatisch befüllt (Docker-Digest aus `release.yml`, LXC-Bundle-Info aus
`lxc-template.yml`); `staging`/`stable` werden manuell per `workflow_dispatch`
(„Promote channel") im Ops-Repo promotet. `obs-update --channel=<name>` löst auf LXC-Hosts
gegen diese Manifeste auf. Details, Schema und Setup: README des Ops-Repos; Rollback-Ablauf:
[release-runbook.md §G](release-runbook.md#g-rollback-kanalzeiger-zurücksetzen).

## 9. Nicht in Scope (nachrüstbar)

Folgendes wurde bewusst für ein späteres Release zurückgestellt:

- CODEOWNERS (sinnvoll wenn das Team wächst)
- Milestone-CI-Enforcement (blockierender Gate)
- Merge Queue
- Automatische Promotion-Zeitfenster / Health-Gates (Kanal-Promotion selbst ist seit
  Phase 2 vorhanden — siehe §8; automatische Zeitfenster/Health-Checks vor einer
  Promotion bleiben zurückgestellt)
- Supply-Chain-Signatur / Attestation
