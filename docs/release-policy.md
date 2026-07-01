# Release Policy

## 1. Branch-Modell

| Branch | Zweck | Erlaubte Änderungen |
|---|---|---|
| `main` | Integrations-Branch für laufende Entwicklung | Features, Bug-Fixes, alle Typen |
| `YYYY.M.x` (z. B. `2026.7.x`) | Stabilisierungs-Branch für ein laufendes Release | Bug-Fixes, Security-Fixes, Release-Engineering, Doku |

- Für jedes geplante Release wird ein eigener Branch geschnitten: `YYYY.M.x`.
- Neue Features auf `main` nach dem Branch-Cut gehören standardmäßig zum **Folge-Release**.
- In `YYYY.M.x` sind keine neuen Features erlaubt — nur Stabilisierung.
- `main` und alle `YYYY.M.x`-Branches sind durch Branch Protection gesichert
  (siehe `.github/BRANCH_PROTECTION_CHECKLIST.md`).

## 2. Release-Zuordnung von Änderungen

- Alles, was **vor** dem Branch-Cut in `main` gemergt ist, ist Kandidat für das aktuelle Release.
- Alles, was **nach** dem Branch-Cut in `main` gemergt wird, gehört standardmäßig zum Folge-Release.
- Eine Aufnahme nach Branch-Cut erfolgt nur bewusst via Backport/Cherry-pick (→ Abschnitt 5).
- Für frühe Merges in `main`, die erst im Folge-Release aktiviert werden sollen:
  Feature Flags verwenden.

## 3. Milestone-Pflicht (Konvention)

- Jede PR soll einem Milestone zugeordnet sein (z. B. `2026.7` oder `2026.8`).
- Kein blockierender CI-Gate — bei einem kleinen Team ist Konvention zuverlässiger als Automation.
- Milestone-Zuordnung ist Reviewpflicht: PR ohne Milestone wird nicht gemergt.

## 4. Tag-Vertrag (CI-enforced)

Release-Tags folgen CalVer: `YYYY.M.PATCH` für Stable-Releases, `YYYY.M.PATCH-RC<N>` für
Release-Kandidaten. Tags außerhalb dieses Formats lösen keinen offiziellen Release-Workflow aus
(gate in `release.yml`).

RC- und Stable-Tags eines Releases dürfen **nur** vom zugehörigen `YYYY.M.x`-Branch erstellt
werden (gate in `release.yml`). Tags auf `main` oder Feature-Branches erzeugen keinen offiziellen
Release.

## 5. Backport-Regeln

Backports in `YYYY.M.x` sind explizite Ausnahmen und brauchen:

1. Label `backport-YYYY.M` (z. B. `backport-2026.7`)
2. Maintainer-Freigabe (mindestens 1 Review)
3. Cherry-pick mit Verweis auf Ursprungs-PR oder Commit-SHA im PR-Body
4. Grüne Required Checks auf dem Zielbranch

Kein Direkt-Push auf `YYYY.M.x` — immer über PR.

## 6. Fix-Reihenfolge bei aktiver Release-Phase

Betrifft ein Fehler das aktuelle Release:

1. Fix zuerst auf `YYYY.M.x` umsetzen und mergen.
2. Danach denselben Fix per Cherry-pick oder Forward-Merge nach `main` übertragen.
3. Sicherstellen, dass beide Branches danach konsistent sind.

Kein „fix direkt auf main und dann hoffen" — Divergenz zwischen `main` und dem Release-Branch
führt zu Chaos beim nächsten Forward-Merge.

## 7. Nicht in Scope (nachrüstbar)

Folgendes wurde bewusst für ein späteres Release zurückgestellt:

- CODEOWNERS (sinnvoll wenn das Team wächst)
- Milestone-CI-Enforcement (blockierender Gate)
- Merge Queue
- Automatische Promotion-Zeitfenster / Health-Gates
- Supply-Chain-Signatur / Attestation
