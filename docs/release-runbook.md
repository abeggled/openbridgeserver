# Release Runbook

Schritt-für-Schritt-Ablauf für Releases, Backports und Hotfixes.
Für Hintergründe und Regeln → `docs/release-policy.md`.

---

## A) Branch-Cut für ein neues Release

1. `main` stabilisieren: Required Checks grün, keine offenen Blocker.
2. Release-Branch schneiden:
   ```bash
   git checkout main
   git pull
   git checkout -b 2026.7.x
   git push -u origin 2026.7.x
   ```
3. Branch Protection für `2026.7.x` in GitHub aktivieren
   (`.github/BRANCH_PROTECTION_CHECKLIST.md` → Abschnitt `YYYY.M.x`).
4. Milestone `2026.7` in GitHub anlegen (falls noch nicht vorhanden).
5. Ab jetzt gehören neue Merges auf `main` standardmäßig zu `2026.8`.

---

## B) Release-Kandidat taggen (RC)

```bash
git checkout 2026.7.x
git pull
git tag 2026.7.0-RC1
git push origin 2026.7.0-RC1
```

`release.yml` prüft automatisch:
- Tag-Format (`YYYY.M.PATCH-RC<N>`)
- Tag-Herkunft (Commit muss auf `2026.7.x` liegen)

---

## C) Stable-Release taggen

```bash
git checkout 2026.7.x
git pull
git tag 2026.7.0
git push origin 2026.7.0
```

Gleiche Gates wie beim RC-Tag.

---

## D) Entwicklung während aktiver Release-Phase

- Normale Feature-Arbeit läuft weiter über PRs nach `main`.
- Neue Features in `main` gehören zum nächsten Release (Milestone `2026.8` setzen).
- Release-Branch nimmt nur freigegebene Fixes/Release-Tasks (Milestone `2026.7`).

---

## E) Backport-Ablauf

1. Entscheiden: Muss der Fix ins aktuelle Release?
2. Label `backport-2026.7` setzen und Maintainer-Freigabe einholen.
3. Cherry-pick auf `2026.7.x`:
   ```bash
   git checkout 2026.7.x
   git pull
   git cherry-pick <commit-sha>
   git push
   ```
4. PR auf `2026.7.x` öffnen (auch für Cherry-picks — kein Direkt-Push).
5. Required Checks auf `2026.7.x` abwarten, dann mergen.
6. Release Notes aktualisieren.

---

## F) Fix im Release-Branch zuerst (Hotfix-Reihenfolge)

1. Fix-PR auf `2026.7.x` erstellen und mergen (Required Checks müssen grün sein).
2. Neuen RC- oder Patch-Tag setzen (→ Abschnitt B/C).
3. Denselben Fix nach `main` übernehmen:
   ```bash
   git checkout main
   git cherry-pick <merge-commit-auf-2026.7.x>
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
- [ ] Required Checks auf `YYYY.M.x` grün
