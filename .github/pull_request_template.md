## Beschreibung

<!-- Was ändert sich und warum? -->

## Typ der Änderung

- [ ] Bug-Fix
- [ ] Neues Feature
- [ ] Release-Engineering / CI / Dokumentation
- [ ] Backport → Label `backport-YYYY.M` setzen, Ziel-Branch `YYYY.M` angeben

## Milestone

<!-- Pflicht: Bitte Milestone im Seitenmenü setzen (z. B. 2026.7 oder 2026.8). -->
<!-- PRs auf einen release-Branch (YYYY.M) brauchen außerdem das Label backport-YYYY.M. -->

## Checkliste

- [ ] Tests für neue oder geänderte Funktionalität vorhanden
- [ ] Lokale Gates grün: `./tools/lint.sh --check`; bei GUI-Änderungen zusätzlich `cd gui && npm run build && npm run test`
- [ ] Neue nutzer-sichtbare Strings in `en.json` **und** `de.json` eingetragen (i18n-Guard)
- [ ] Release Notes in `RELEASENOTES.md` aktualisiert (wenn nutzer-relevant)
