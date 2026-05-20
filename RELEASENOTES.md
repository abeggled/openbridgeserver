# Changes

## 2026.6.0
### Breaking changes 🚨
* none

### New features 💡
* Adapter: The KNX adapter now also supports TCP tunneling mode and Secure support via import of the .knxkeys file. https://github.com/abeggled/openbridgeserver/issues/14
* Adapter: The adapter "Anwesenheitssimulation" allows automatic replay of switching states of defined objects during absence with an offset of n days. https://github.com/abeggled/openbridgeserver/issues/344
* Adapter: New SNMP adapter with support for protocol versions v1, v2c, and v3. https://github.com/abeggled/openbridgeserver/issues/381
* Backend: Object hierarchy with multiple roots for different purposes, including manual creation or ETS import for group address, building, and trade structures. https://github.com/abeggled/openbridgeserver/issues/355
* Backend: For objects used in the logic module, the links section has been extended with a direct link to the corresponding logic sheet. https://github.com/abeggled/openbridgeserver/issues/366
* Backend: Extended backup functionality. Everything is now backed up including the visualization, the SQLite DB can also be restored, and an automatic backup function has been added. https://github.com/abeggled/openbridgeserver/issues/373
* Backend: Utilities for parallel operation of multiple OBS instances, such as displaying a banner for easier differentiation. https://github.com/abeggled/openbridgeserver/issues/406
* Backend: Object hierarchy allow to change the startpoint in the tree https://github.com/abeggled/openbridgeserver/issues/443
* Logicmodule: Functional Block: "iCalendar" filtering by summary, location, and description. https://github.com/abeggled/openbridgeserver/issues/350
* Visu: Floor plan widget with the ability to place mini widgets on the floor plan. https://github.com/abeggled/openbridgeserver/issues/228
* Visu: RTR Widget: Cilmate control (A/C) mode added for use with correct DPT 20.105 https://github.com/abeggled/openbridgeserver/issues/461
  
### Fixes 🐞
* General: Proxmox LXC, confusing checksum field content within release notes. https://github.com/abeggled/openbridgeserver/issues/375
* Backend: The adapter page automatically reloaded every few seconds, making configuration difficult. https://github.com/abeggled/openbridgeserver/issues/394
* Logicmodule: The object selector now uses the entire available window space. https://github.com/abeggled/openbridgeserver/issues/345
* Visu: History widget now updates automatically when new values arrive via WebSocket. https://github.com/abeggled/openbridgeserver/issues/408
* Visu: RTR Widget now use correct values for room controller (heating) DPT 20.102 https://github.com/abeggled/openbridgeserver/issues/461

## 2026.5.2
### Breaking changes 🚨
* none

### New features 💡
* none

### Fixes 🐞
* General: Missing Docker Image for ARM64 https://github.com/abeggled/openbridgeserver/issues/361
* Adapter: The MQTT adapter did not send an MQTT client ID; the adapter now generates a random one, the client ID and TLS settings are now configurable https://github.com/abeggled/openbridgeserver/issues/363
* Adapter: Nested JSON structures could not be processed by the JSON selector in the MQTT adapter and displayed for selection https://github.com/abeggled/openbridgeserver/issues/356
* Visu: Some Visu widgets incorrectly displayed a red exclamation mark, which actually indicates a missing object after an import https://github.com/abeggled/openbridgeserver/issues/342

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
  
