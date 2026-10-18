---
title: Icons
---

# Icons

Der Icons-Tab findest du im Admin-GUI unter **Einstellungen → Icons**.

## Icon-Bibliothek {#settings-icons}

Zeigt alle installierten Icons als durchsuchbares Raster. Mehrfachauswahl per Klick
ermöglicht Export (als Sammlung) oder Löschen der ausgewählten Icons; „Alle wählen"/„Alle
abwählen" wirkt auf die aktuell gefilterte/durchsuchte Liste.

## SVG Icons importieren {#settings-icons-import}

Icons per Drag & Drop oder Dateiauswahl hochladen — einzelne SVG-Dateien oder ein ZIP-Archiv
mit mehreren SVGs. Jede Datei wird auf gültiges SVG-Format geprüft, unabhängig von der
Dateiendung.

## KNX UF Iconset importieren {#settings-icons-knxuf}

Lädt alle Icons aus dem `ha-knx-uf-iconset`-Projekt direkt aus dessen GitHub-Repository und
speichert sie mit dem Präfix `kuf_` in der Icon-Bibliothek. Bereits vorhandene `kuf_`-Icons aus
einem früheren Import werden dabei überschrieben.

## FontAwesome importieren {#settings-icons-fontawesome}

Importiert Icons direkt von FontAwesome 7. Ohne API Key werden kostenlose Free-Icons
verwendet; mit einem (persistent gespeicherten) FontAwesome-PRO-API-Key stehen zusätzlich
PRO-Icons und weitere Styles (Light, Thin, Duotone neben Solid/Regular/Brands) zur Verfügung.
Icon-Namen werden kommagetrennt eingegeben; der importierte Dateiname enthält automatisch den
gewählten Style (z. B. `abacus-solid.svg`).
