# HomeKit/Yahka-Integration für OBS

## Warum dieses Projekt existiert

Viele Installationen nutzen OBS bereits als zentrale Plattform, in der Räume, Widgets,
Datenpunkte, KNX-Bindings und Automationslogik zusammenlaufen. Gleichzeitig wird
Apple Home häufig über ioBroker und Yahka angebunden, da diese bereits eine
praktische HomeKit-Bridge bereitstellen.

Ohne einen OBS-orientierten Integrations-Workflow ist das Onboarding in Apple Home
oft manuell und fehleranfällig:

- HomeKit-State-IDs werden von Hand erstellt  
- bestehende OBS- und KNX-Datenpunkte werden dupliziert statt wiederverwendet  
- Schreib- und Statuspfade laufen auseinander  
- Benennung, Raumstruktur und Verhalten werden inkonsistent  
- Fallback- und Restore-Verhalten sind später schwer nachvollziehbar  

Dieses Projekt ergänzt OBS um eine strukturierte Migrations- und Helper-Schicht,
sodass bestehende VISU- und Adaptermodelle reproduzierbar in stabile,
HomeKit-seitige ioBroker-States und Bindings überführt werden können.

## Kernidee

Die Kernidee ist einfach:

`OBS bleibt die Quelle der Struktur. ioBroker/Yahka bleibt die HomeKit-Bridge.`

OBS kennt bereits Räume, Widgets, Datenpunkte und Protokollbindungen der Installation.
Damit kann OBS aus dem VISU-Baum einen überprüfbaren HomeKit-Plan generieren,
anstatt den Nutzer zu zwingen, dieselbe Struktur ein zweites Mal in Yahka nachzubauen.

Die Integration ersetzt Yahka nicht, sondern bereitet das Datenmodell vor, das Yahka benötigt:

- stabile ioBroker-State-IDs  
- konsistente Raum- und Accessory-Benennung  
- explizite Lese-/Schreibrichtung pro State  
- Wiederverwendung bestehender KNX/ETS-Datenpunkte  
- klare Erkennung nicht unterstützter oder riskanter Fälle vor produktiven Änderungen  

## Was OBS ergänzt

Die neue OBS-Funktionalität ist ein experimenteller Migrations-Helper mit zwei Hauptschritten.

### 1. Vorschau (Preview)

OBS analysiert den bestehenden VISU-Baum und erzeugt eine überprüfbare
HomeKit/Yahka-Mapping-Vorschau.

Die Vorschau zeigt:

- Räume und Accessories aus der VISU-Hierarchie  
- normalisierte ioBroker-State-IDs  
- den vorgesehenen HomeKit-Service-Typ  
- Bindungsrichtung pro State  
- KNX-Status-/Schreib-Datenpunkte (sofern vorhanden)  
- Warnungen für nicht unterstützte Widgets oder riskante Mappings  
- geschätzte Anzahl an Accessories und Bridge-Limit-Auslastung  

Die Vorschau ist bewusst read-only.

### 2. Kontrollierte Anwendung (Apply)

Nach der Prüfung kann OBS einen kontrollierten Apply-Schritt ausführen.

Dabei kann:

- fehlende OBS-Datenpunkte nur bei Bedarf angelegt werden  
- bestehende KNX/ETS-Datenpunkte wiederverwendet werden  
- ioBroker-Bindings für die generierten State-IDs erstellt werden  
- optional die entsprechenden ioBroker-States erzeugt werden  

Der Apply-Prozess erfolgt standardmäßig zunächst als Dry-Run und ist
möglichst idempotent ausgelegt.

## Nutzen über eine einzelne Installation hinaus

Der Mehrwert ist nicht installationsspezifisch. Allgemein relevant für OBS ist:

- Nutzung des VISU-Modells für externe Integrationsplanung  
- Generierung stabiler, überprüfbarer State-Namensräume  
- Anbindung externer Systeme an bestehende Datenpunkte statt Duplikation  
- Vorschau risikobehafteter Integrationen vor produktiven Änderungen  
- klare Definition von Lese-/Schreibsemantik für externe Bridges  

Dieses Muster ist überall sinnvoll, wo OBS als System-of-Record dient und ein
anderes System die Benutzeroberfläche bildet.

## Aktueller Umfang

Aktuell werden folgende Widget-Familien unterstützt:

- `Licht` -> `Lightbulb`  
- `Toggle` -> `Switch` oder `Outlet`  
- `Fenster` -> `ContactSensor`  
- `Rolladen` -> `WindowCovering`  
- `RTR` -> `Thermostat`  
- `ValueDisplay` -> Temperatur- oder Feuchtigkeitssensor  

Nicht unterstützte Widgets werden nicht ignoriert, sondern explizit angezeigt.

## Designprinzipien

- Wiederverwenden statt neu erstellen  
- Vorschau vor Schreiben  
- Explizite Richtung (read/write)  
- Stabile Benennung  
- Neutrale, installationsunabhängige Defaults  

## Beziehung zum nativen ioBroker-Adapter

Das Projekt baut auf dem bestehenden ioBroker-Adapter in OBS auf.

Dieser bietet:

- Verbindung zu ioBroker-Instanzen  
- Zugriff auf States  
- Live-Subscriptions  
- Schreibzugriff  
- programmgesteuerte State-Erstellung  
- robuste Reconnect-Mechanismen  

Der HomeKit/Yahka-Helper ist somit ein Workflow auf Adapterbasis,
kein eigener HomeKit-Stack.

## GUI-Ausrichtung

Platzierung:

`Adapter -> ioBroker -> HomeKit/Yahka`

Workflow-Stufen:

- Preview  
- Plan / Dry-Run  
- Apply  

## Was dieses Projekt nicht ist

- kein Ersatz für Yahka  
- kein HomeKit-Pairing-Manager  
- kein Apple-Home-Editor  
- keine „magischen Defaults“ für komplexe Themen  

## Praktischer Nutzen

OBS wird zur zentralen Instanz für:

- strukturiertes HomeKit-Onboarding  
- kontrollierte Migrationen  
- Vermeidung von Datenpunkt-Duplikaten  
- Dokumentation der Integrationslogik  
- Wartung und Troubleshooting  

## Zusammenfassung

Der Kern ist nicht:

„HomeKit direkt in OBS integrieren“

Der Kern ist:

- OBS als strukturelle Quelle nutzen  
- ioBroker/Yahka als Bridge verwenden  
- Integration planbar und überprüfbar machen  
- bestehende Datenmodelle erhalten  

Damit entsteht ein allgemeines Integrationsmuster für OBS-basierte Systeme.
