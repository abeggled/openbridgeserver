---
title: Icons
---

# Icons

Der Icons-Tab findest du im Admin-GUI unter **Einstellungen → Icons**.

## Icon-Bibliothek {#settings-icons}

Zeigt alle installierten Icons als durchsuchbares Raster. Mehrfachauswahl per Klick
ermöglicht Export (als Sammlung) oder Löschen der ausgewählten Icons; „Alle wählen"/„Alle
abwählen" wirkt auf die aktuell gefilterte/durchsuchte Liste.

## Icons importieren {#settings-icons-import}

Icons per Drag & Drop oder Dateiauswahl hochladen — einzelne SVG-, PNG- oder JPEG-Dateien oder
ein ZIP-Archiv mit mehreren solchen Dateien. Jede Datei wird anhand ihres Inhalts geprüft,
unabhängig von der Dateiendung; Name des Icons ist der Dateiname ohne Endung.

PNG- und JPEG-Bilder (max. 1 MB pro Bild) werden als SVG gespeichert, in das das Bild
eingebettet ist; Breite und Höhe des Bildes bleiben dabei als Seitenverhältnis erhalten. Auch
SVGs, in die ein Bitmap eingebettet ist (z. B. ein in Inkscape eingefügtes Foto), behalten das
Bild beim Import. Wie farbige SVGs erscheint ein solches Icon in der Visu nur dort in seinen
Originalfarben, wo ein Widget die Option „SVG-Icon-Farbe erhalten" anbietet und sie aktiviert
ist; sonst wird es als einfarbige Silhouette dargestellt.

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
