# Changes

## 2026.5.2
### Breaking changes 🚨
* none

### New features 💡
* none
  
### Fixes 🐞
* #361: General: Missing Docker Image for ARM64
* #363: Adapter: The MQTT adapter did not send an MQTT client ID; the adapter now generates a random one, the client ID and TLS settings are now configurable
* #356: Adapter: Nested JSON structures could not be processed by the JSON selector in the MQTT adapter and displayed for selection
* #342: Visu: Some Visu widgets incorrectly displayed a red exclamation mark, which actually indicates a missing object after an import

## 2026.5.1
### New features:
* General: LXC template for ARM architectures
* Adapter: ioBroker
* Logicmodule: Functional Block: "Substring"
* Logicmodule: Functional Block: "Zufallswert"
* Logicmodule: Functional Block: "Mittelwert, gleitender Mittelwert (1m,1h,1d,7d,14d,30d,180d,360d)"
* Logicmodule: Duplication, Import, Export of logic canvas
* Visu Widget "Stufenschalter"
* Visu Widget "Uhr" with analog, digital an word-clock including timezones
* Visu Widget "Thermostat" with HVAC modes and current temperature
* Visu Widget "Wetter" currently supported: openweathermap.org One Call API 3.0
* Visu: Duplication, Import, Export of visu sites
  
### Fixes:
* General: Fix used tags at docker images
* General: Implement contract tests for dependencies
* Backend: History give only last 1000 entries now default 10'000 with amximum of 100'000
* Adapter ioBroker browse/import preview are blocked when the instance status lags behind the live socket connection
* Adapter: "Zeitschaltuhr" support for multiple "Schaltpunkte" and own public holidays
* Logicmodule: Functional Block: Sommer/Winter Umschaltung nach DIN Functional Block does now work as expected
* Logicmodule: Functional Block: Read object / Write object: Renamed objects will be reflected in the Logicmodule now
* Visu Widget: Enhancment Roof Window Widget (new Velux-Type), and new "Zweitürer (L/R)"
* Visu Widget "Verlauf" has now the possibility to display multiple graphs with two units (left/right)
* Visu Widget "Zeitschaltuhr" supports multiple "Schaltpunkte" and oother new functions of the adapter

### Breaking changes:
* none
  
