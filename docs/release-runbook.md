# Release Runbook

Schritt-für-Schritt-Ablauf für Releases, Backports und Hotfixes.
Für Hintergründe und Regeln → `docs/release-policy.md`.

---

## A) `.0`-Release (aus `main`)

1. `main` stabilisieren: Required Checks grün, keine offenen Blocker.
2. `RELEASENOTES.md` vollständig und reviewed.
3. RC-Tag direkt auf `main` setzen:
   ```bash
   git checkout main
   git pull
   git tag 2026.7.0-RC1
   git push origin 2026.7.0-RC1
   ```
4. `release.yml` prüft automatisch:
   - Tag-Format (`YYYY.M.PATCH-RC<N>`)
   - Tag-Herkunft: `2026.7.0-RC1` → muss auf `main` liegen
5. Nach erfolgreichem Test: Stable-Tag setzen:
   ```bash
   git tag 2026.7.0
   git push origin 2026.7.0
   ```
6. CI erstellt automatisch einen PR auf `main`, der `## 2026.8.0` in `RELEASENOTES.md` vorbereitet.
   → Diesen PR reviewen und mergen, bevor der erste Feature-PR für 2026.8 einläuft.

   **Bekannte Einschränkung:** Der Auto-PR leitet die Folgeversion strikt aus „Monat+1" ab
   (konsistent mit der Monats-CalVer-Policy). Wird ein Monat ausgelassen — z. B. erscheint nach
   `2026.6.0` direkt `2026.8.0` — legt der Job trotzdem einen nie released `## 2026.7.0`-Abschnitt
   an. In diesem Fall den Auto-PR schließen und den Abschnitt manuell auf die tatsächliche
   Folgeversion korrigieren, bevor gemergt wird.

---

## B) Bugfix-Branch anlegen (nur bei Bedarf)

Wenn nach dem `.0`-Release ein Patch-Release notwendig ist:

1. Bugfix-Branch aus dem `.0`-Tag schneiden:
   ```bash
   git checkout 2026.7.0
   git checkout -b 2026.7
   git push -u origin 2026.7
   ```
2. Branch Protection für `2026.7` in GitHub aktivieren
   (`.github/BRANCH_PROTECTION_CHECKLIST.md` → Abschnitt `YYYY.M`).
3. Milestone `2026.7` in GitHub anlegen (falls noch nicht vorhanden).

---

## C) Patch-Release taggen

```bash
git checkout 2026.7
git pull
git tag 2026.7.1
git push origin 2026.7.1
```

`release.yml` prüft automatisch:
- Tag-Format (`YYYY.M.PATCH`)
- Tag-Herkunft: `2026.7.1` → muss auf Branch `2026.7` liegen

---

## D) Entwicklung während aktiver Release-Phase

- Normale Feature-Arbeit läuft weiter über PRs nach `main`.
- Neue Features in `main` gehören zum nächsten Release (Milestone `2026.8` setzen).
- Der `2026.7`-Branch (falls angelegt) nimmt nur freigegebene Fixes auf.

---

## E) Backport-Ablauf

1. Entscheiden: Muss der Fix ins aktuelle Release? Bugfix-Branch vorhanden?
2. Falls kein Bugfix-Branch vorhanden: erst Abschnitt B ausführen.
3. Label `backport-2026.7` setzen und Maintainer-Freigabe einholen.
4. Separaten Backport-Branch anlegen und Cherry-pick durchführen:
   ```bash
   git fetch origin 2026.7
   git checkout -b backport/2026.7/<fix-name> origin/2026.7
   git cherry-pick <commit-sha>
   git push -u origin backport/2026.7/<fix-name>
   ```
5. PR auf `2026.7` öffnen (Ziel-Branch: `2026.7`, kein Direkt-Push auf den Release-Branch).
6. Required Checks auf dem PR abwarten, dann mergen.
7. Release Notes aktualisieren.

---

## F) Fix im Bugfix-Branch zuerst (Hotfix-Reihenfolge)

1. Fix-PR auf `2026.7` erstellen und mergen (Required Checks müssen grün sein).
2. Neuen RC- oder Patch-Tag setzen (→ Abschnitt C).
3. Denselben Fix nach `main` übernehmen:
   ```bash
   git checkout main
   git cherry-pick <merge-commit-auf-2026.7>
   # oder Forward-Merge wenn passend
   git push
   ```
4. Verifizieren, dass beide Branches konsistent sind.

---

## G) Rollback (Kanalzeiger zurücksetzen)

*Gilt für Phase 2 — kanalbasierter Update-Pfad (→ #945).*

Bis Phase 2 aktiv ist: LXC-Rollback durch manuelles Ausführen von
`obs-update` und Auswahl der vorherigen stabilen Version.

---

## H) Checkliste vor dem Stable-Tag

- [ ] Alle geplanten Fixes für dieses Release gemergt
- [ ] `RELEASENOTES.md` vollständig und reviewed
- [ ] Kein offener Blocker-Incident
- [ ] Mindestens ein RC erfolgreich getestet
- [ ] Required Checks auf `main` (für `.0`) resp. `YYYY.M` (für Patch) grün
