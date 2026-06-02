# Changes

## 2026.6.0
### Breaking changes 🚨
* none

### New features ✨
* Adapter: The KNX adapter now also supports TCP tunneling mode and Secure support via import of the .knxkeys file. https://github.com/abeggled/openbridgeserver/issues/14
* Adapter: Add detailed connection error messages for KNX adapter: https://github.com/abeggled/openbridgeserver/issues/466
* Adapter: The adapter "Anwesenheitssimulation" allows automatic replay of switching states of defined objects during absence with an offset of n days. https://github.com/abeggled/openbridgeserver/issues/344
* Adapter: New SNMP adapter with support for protocol versions v1, v2c, and v3. https://github.com/abeggled/openbridgeserver/issues/381
* Backend: Object hierarchy with multiple roots for different purposes, including manual creation or ETS import for group address, building, and trade structures. https://github.com/abeggled/openbridgeserver/issues/355
* Backend: For objects used in the logic module, the links section has been extended with a direct link to the corresponding logic sheet. https://github.com/abeggled/openbridgeserver/issues/366
* Backend: Extended backup functionality. Everything is now backed up including the visualization, the SQLite DB can also be restored, and an automatic backup function has been added. https://github.com/abeggled/openbridgeserver/issues/373
* Backend: Utilities for parallel operation of multiple OBS instances, such as displaying a banner for easier differentiation. https://github.com/abeggled/openbridgeserver/issues/406
* Backend: Object hierarchy allow to change the startpoint in the tree https://github.com/abeggled/openbridgeserver/issues/443
* Backend: Object hierarchy startpoint can be defined and the full path is displayed on mouseover https://github.com/abeggled/openbridgeserver/issues/443
* Backend: Extension of the monitor with extremely extensive filtering options https://github.com/abeggled/openbridgeserver/issues/36
* Backend: Monitor/Ringbuffer retention, storage model, query/filter semantics and filtersets API. https://github.com/abeggled/openbridgeserver/issues/384 https://github.com/abeggled/openbridgeserver/issues/385 https://github.com/abeggled/openbridgeserver/issues/386 https://github.com/abeggled/openbridgeserver/issues/387 https://github.com/abeggled/openbridgeserver/issues/388 https://github.com/abeggled/openbridgeserver/issues/389
* Backend: Monitor/Ringbuffer CSV export for complete filtered results. https://github.com/abeggled/openbridgeserver/issues/390
* QA/CI: Monitor baseline, characterization and coverage/dependency audit tasks. https://github.com/abeggled/openbridgeserver/issues/383 https://github.com/abeggled/openbridgeserver/issues/428 https://github.com/abeggled/openbridgeserver/issues/429 https://github.com/abeggled/openbridgeserver/issues/439
* Backend: Possibility to migrate all objects of an adapter to a new one of the same type https://github.com/abeggled/openbridgeserver/issues/419
* Backend: Log viewer with filtering options https://github.com/abeggled/openbridgeserver/issues/452
* Backend: Hierarchy Manager use current names as example to better understand the changes https://github.com/abeggled/openbridgeserver/issues/467
* Backend: Full internationalization (i18n) of the gui and visu, currently supported languages are DE and EN https://github.com/abeggled/openbridgeserver/issues/351
* Backend: Binding migration between adapter instances (bulk migration workflow). https://github.com/abeggled/openbridgeserver/pull/513
* Backend: Filtersets with fine-grained ownership (admin/owner edit rights and per-user visibility). https://github.com/abeggled/openbridgeserver/pull/493
* Backend: Hierarchy wording was unified across the UI (Hierarchie/Wurzelknoten/Ebene). https://github.com/abeggled/openbridgeserver/pull/490
* Backend: Datapoint list/object browser can be filtered by one or more adapters. https://github.com/abeggled/openbridgeserver/pull/515
* Backend: Instance banner and configurable host ports for parallel local stacks. https://github.com/abeggled/openbridgeserver/pull/405
* Frontend: Monitor core UI, filter builder, time filter UX, editor/topbar/table improvements and CSV export UI. https://github.com/abeggled/openbridgeserver/issues/391 https://github.com/abeggled/openbridgeserver/issues/392 https://github.com/abeggled/openbridgeserver/issues/426 https://github.com/abeggled/openbridgeserver/issues/427 https://github.com/abeggled/openbridgeserver/issues/430 https://github.com/abeggled/openbridgeserver/issues/432 https://github.com/abeggled/openbridgeserver/issues/435 https://github.com/abeggled/openbridgeserver/issues/436 https://github.com/abeggled/openbridgeserver/issues/437 https://github.com/abeggled/openbridgeserver/issues/438
* Frontend: Monitor filterset schema/colors, unit column and datapoint path label integration. https://github.com/abeggled/openbridgeserver/issues/431 https://github.com/abeggled/openbridgeserver/issues/434 https://github.com/abeggled/openbridgeserver/issues/433
* Logic engine: Option to disable a logic sheet https://github.com/abeggled/openbridgeserver/issues/422
* Logic engine: Functional Block: "iCalendar" filtering by summary, location, and description. https://github.com/abeggled/openbridgeserver/issues/350
* Logic engine: Functional Block: "XML Extractor" now has multiple outputs from single input https://github.com/abeggled/openbridgeserver/pull/469
* Logic engine: Functional Block: "JSON Extractor" now has multiple outputs from single input https://github.com/abeggled/openbridgeserver/pull/468
* Logic engine: Functional Block: API client nodes can now load optional headers and bearer tokens from secret files. https://github.com/abeggled/openbridgeserver/pull/581
* Visu: Add background images https://github.com/abeggled/openbridgeserver/issues/481
* Visu: Floor plan widget with the ability to place mini widgets on the floor plan https://github.com/abeggled/openbridgeserver/issues/228
* Visu: History widget: select time period direct from widget https://github.com/abeggled/openbridgeserver/issues/413
* Visu: Value display widget: Added Gauge Mode https://github.com/abeggled/openbridgeserver/issues/416
* Visu: Bar chart widget: Added new horizontal bar chart widget: https://github.com/abeggled/openbridgeserver/issues/417
* Visu: History widget: Added bar chart mode https://github.com/abeggled/openbridgeserver/issues/418
* Visu: RTR widget: Cilmate control (A/C) mode added for use with correct DPT 20.105 https://github.com/abeggled/openbridgeserver/issues/461
* Visu: RTR widget: color gradient added https://github.com/abeggled/openbridgeserver/issues/465
* Visu: Gauge mode for value display widget (arc/circle variants). https://github.com/abeggled/openbridgeserver/pull/421
* Visu: Bar chart mode for history/chart widget. https://github.com/abeggled/openbridgeserver/pull/444
* Visu: Widgets können per Drag & Drop aus der Palette direkt an eine bestimmte Position auf der Seite gezogen werden; eine blaue Vorschau zeigt die Zielposition. Klick auf ein Widget fügt es weiterhin automatisch an der ersten freien Position ein. Die Widget-Liste ist jetzt sprachspezifisch alphabetisch sortiert. https://github.com/abeggled/openbridgeserver/issues/667

### Fixes 🐞
* Adapter: KNX IP Secure now works correctly in Docker bridge networks — credentials are extracted directly from the .knxkeys file and passed explicitly to xknx, bypassing the internal UDP DescriptionRequest that fails without host networking. Connection errors now include actionable hints (Docker network mode, gateway tunnel-slot exhaustion). https://github.com/abeggled/openbridgeserver/issues/393
* Backend: Complete remaining UI translation fixes after i18n rollout. https://github.com/abeggled/openbridgeserver/pull/542
* Backend: Validate `DataValueEvent` payloads before bridge propagation. https://github.com/abeggled/openbridgeserver/pull/519
* Backend: Ringbuffer pause/resume race condition stabilized. https://github.com/abeggled/openbridgeserver/pull/509
* Backend: InfluxDB v3 writes now use correct `db` query parameter. https://github.com/abeggled/openbridgeserver/pull/511
* Backend: The adapter page automatically reloaded every few seconds, making configuration difficult. https://github.com/abeggled/openbridgeserver/issues/394
* Backend: Fix view permissions of Demo User https://github.com/abeggled/openbridgeserver/issues/471
* Backend: History default window changed from 24h to 7d and is now configurable via `history.default_window_hours` (Settings → Historie DB). https://github.com/abeggled/openbridgeserver/pull/582
* Backend: KNX UF Iconset import — one-click import of all 940 KNX UF icons from ha-knx-uf-iconset directly into the icon library (prefix `kuf_`); re-import overwrites existing icons. https://github.com/abeggled/openbridgeserver/issues/677
* Backend: ETS import of password-protected .knxproj files now works correctly: Gewerke (trades) are parsed from the decrypted inner ZIP, ETS6 wrong-password errors ("Bad HMAC check") are properly reported as password errors, GA and location parsing run in parallel (non-blocking), frontend timeout raised to 300 s for large files, and error messages are fully localized via error codes. https://github.com/abeggled/openbridgeserver/issues/679
* Backend: Fixed MQTT binding edit/create dialog becoming blank when switching to write direction; adapter-type resolution and i18n handling in BindingForm were hardened. https://github.com/abeggled/openbridgeserver/issues/656
* Visu: History (Chart) widget and Value Display widget time-range dropdowns now show translated labels instead of raw i18n key strings. https://github.com/abeggled/openbridgeserver/issues/662
* General #375: Proxmox LXC, confusing checksum field content within release notes. https://github.com/abeggled/openbridgeserver/issues/375
* Logic engine: The object selector now uses the entire available window space. https://github.com/abeggled/openbridgeserver/issues/345
* Logic engine: Sommer/Winter (DIN) block now fills T1/T2/T3 slots correctly when sensors report at intervals that do not hit hours 7, 12, or 22 exactly (e.g. every 2 or 4 hours). "First-crossing" semantics: each slot is captured on the first measurement at or after its target hour, so daily_avg is always computed and heating mode switches reliably. https://github.com/abeggled/openbridgeserver/issues/548
* GUI: Missing i18n in several areas of the Admin GUI: all port and node labels in the Logic Engine node canvas are now fully translated and react to locale switching; the Hierarchy Manager dialog has been fully internationalised (all hardcoded German strings replaced). https://github.com/abeggled/openbridgeserver/issues/668
* Logic engine: Functional Block "Sommer/Winter (DIN)" completely rewritten: measurement times corrected to DIN Mannheimer standard (T1 = 07:00, T2 = 14:00, T3 = 21:00); single configurable threshold temperature (default 14 °C) with hysteresis (default 2 °C) replaces separate summer/winter thresholds; heating decision based on daily average; debug ports T1/T2/T3 now persist their values after the daily average is computed; missing slots are automatically recovered from history after a server restart. https://github.com/abeggled/openbridgeserver/issues/665
* Backend Security (Upstream PR #683): prevent Uvicorn access logs from being exposed through the in-memory log stream.
* Security (Upstream PR #576): prevent SSRF/data exfiltration in iCal URL fetching by enforcing public-network URL validation and streamed size limits.
* Security (Upstream PR #563): harden Pushover `image_url` fetch against non-global targets, event-loop DNS blocking, and DNS rebinding
* Security: Preserve legacy `OPENTWS_*`/`OPENTWS_CONFIG` compatibility with case-insensitive `OBS_CONFIG` precedence and keep `opentws.db` fallback active even with partial `database.*` overrides to avoid unintended default-admin re-bootstrap on upgrades. https://github.com/abeggled/openbridgeserver/pull/554
* Security: Require admin privileges for datapoint and logic mutations. https://github.com/abeggled/openbridgeserver/pull/456
* Security: Restrict datapoint writes to widgets referenced by the current page. https://github.com/abeggled/openbridgeserver/pull/457
* Security: Restrict anonymous datapoint writes to page widget membership. https://github.com/abeggled/openbridgeserver/pull/458
* Security: Enforce admin or page-scoped authorization for datapoint writes. https://github.com/abeggled/openbridgeserver/pull/459
* Security: Stop exposing WebSocket JWTs in URL query strings. https://github.com/abeggled/openbridgeserver/pull/518
* Security: Restore public/protected viewer bootstrap reads and WebSocket connectivity without forcing JWT, reconnect page-scoped WS sessions on context changes, include WidgetRef target datapoints in anonymous allowlists, restrict anonymous WS allowlists to explicit datapoint fields, and stop passing JWT/session credentials via WS query params. https://github.com/abeggled/openbridgeserver/pull/553
* Security (Upstream PR #570): restore authenticated WebSocket access via header/subprotocol/API-key auth while keeping URL token transport disabled.
* Security: Prevent logic formula sandbox escape via custom round helper. https://github.com/abeggled/openbridgeserver/pull/504
* Security: Validate imported binding formulas to prevent untrusted formula execution. https://github.com/abeggled/openbridgeserver/pull/505
* Security: Reject active/scriptable SVG payloads on icon/config import to prevent stored XSS. https://github.com/abeggled/openbridgeserver/pull/558
* Security: Bound write-router value cache to mitigate MQTT payload-retention DoS risk. https://github.com/abeggled/openbridgeserver/pull/524
* Security (Upstream PR #528): harden AST sandboxing in the logic executor to prevent sandbox escapes.
* Security: Harden SVG icon import sanitization (obfuscated javascript href, deep nesting guard, stable `<svg>` serialization, blocked SMIL animation tags, and DOCTYPE rejection), make ZIP imports atomic on sanitize errors, preserve API-key flows across username changes, and allow imports for authenticated users. https://github.com/abeggled/openbridgeserver/pull/555
* Security: Harden LXC first-boot and release handling (per-container JWT secret, stricter env/tag handling). https://github.com/abeggled/openbridgeserver/pull/455 https://github.com/abeggled/openbridgeserver/pull/506 https://github.com/abeggled/openbridgeserver/pull/512
* Security: (Upstream PR #575): prevent stored XSS via SVG icon rendering in Stufenschalter widget
* Security: (Upstream PR #568): prevent stored XSS via SVG icon rendering (Visu)
* Security: (Upstream PR #565): prevent stored XSS via obfuscated `javascript:`/`data:` URLs in Toggle SVG icon rendering
* Security: (Upstream PR #572): prevent stored XSS by rejecting SVG uploads in the background catalog.
* Security: (Upstream PR #551): sanitize markdown HTML rendering in Text widget to prevent stored XSS.
* Test stability: Monitor/Ringbuffer E2E scenarios stabilized. https://github.com/abeggled/openbridgeserver/pull/494
* Visu: Internal API base URL usage fixed for E2E/runtime alignment. https://github.com/abeggled/openbridgeserver/pull/484
* Visu: History widget now updates automatically when new values arrive via WebSocket. https://github.com/abeggled/openbridgeserver/issues/408
* Visu: RTR Widget now use correct values for room controller (heating) DPT 20.102 https://github.com/abeggled/openbridgeserver/issues/461
* Visu: Floorplan Widget: positioning broken if floorplan is rotated https://github.com/abeggled/openbridgeserver/issues/440
* Visu: Slider widget values are now written on pointer release and keyboard commit, avoiding missed writes in browsers that do not reliably fire change after dragging. https://github.com/abeggled/openbridgeserver/pull/559
* Visu: History widget displays translated labels instead of variable name

### Known Issues 🔔
* none

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
* Logicmodule Security (Upstream PR #562): harden notify_pushover image_url fetching against DNS-rebinding SSRF bypass
* Security: Sanitize uploaded SVG icon content before ValueDisplay `v-html` injection to prevent stored XSS.
* General: Fix used tags at docker images
* General Security (Upstream PR #567): prevent tag-name code injection in release workflow
* General: Implement contract tests for dependencies
* General Security (Upstream PR #574): harden LXC updater by verifying app bundle checksums against the original release artifact filename
* Backend: History give only last 1000 entries now default 10'000 with amximum of 100'000
* Adapter ioBroker browse/import preview are blocked when the instance status lags behind the live socket connection
* Adapter ioBroker Security (Upstream PR #566): skip watchdog resync publishes when state reads fail
* Adapter Home Assistant Security (Upstream PR #560): remove startup initial-read REST fetch to prevent SSRF via binding-controlled entity IDs
* Adapter: "Zeitschaltuhr" support for multiple "Schaltpunkte" and own public holidays
* Logicmodule: Functional Block: Sommer/Winter Umschaltung nach DIN Functional Block does now work as expected
* Logicmodule: Functional Block: Read object / Write object: Renamed objects will be reflected in the Logicmodule now
* Logicmodule Security (Upstream PR #573): allow safe math constants (e.g. math.pi/math.e) in formula validation
* Visu Widget: Enhancment Roof Window Widget (new Velux-Type), and new "Zweitürer (L/R)"
* Visu Widget "Verlauf" has now the possibility to display multiple graphs with two units (left/right)
* Visu Widget "Zeitschaltuhr" supports multiple "Schaltpunkte" and oother new functions of the adapter
* Visu Security (Upstream PR #564): prevent stored XSS in IFrame widget by enforcing http/https URLs and sanitizing sandbox permissions (Visu)
* Visu Security (Upstream PR #561): prevent stored XSS via SVG icon rendering (Visu)

### Breaking changes:
* none
  
