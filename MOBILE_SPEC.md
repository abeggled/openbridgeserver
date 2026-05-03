# OBS Mobile App — Specification

**Version:** 0.1 (pre-implementation)
**Lizenz:** MIT
**Teil von:** OBS Monorepo (`mobile/`)
**Stand:** 2026-05-03

---

## 1. Projektziel

Die OBS Mobile App bringt die OBS Visu als native App auf iOS und Android. Sie bildet **ausschliesslich die Visu** ab — keine OBS-Konfiguration, keine Datenpunkt-Verwaltung, keine Systemeinstellungen. Der einzige native Screen ist die Server-Verbindungskonfiguration; alles andere ist die bestehende Visu-SPA.

Feature-Parität zwischen iOS und Android ist durch die Architektur garantiert: beide Plattformen führen denselben Code aus.

---

## 2. Technologie-Entscheidung: Capacitor

### Begründung

Die OBS Visu ist eine Vue 3 + Vite SPA. Capacitor verpackt diese WebView in eine native iOS- und Android-App, ohne eine zweite Codebasis zu erzwingen. Der gesamte Visu-Code — Widgets, WebSocket-Integration, Pinia-Stores, Widget-Registry — läuft unverändert weiter.

| Kriterium | Capacitor | React Native / Flutter |
|---|---|---|
| Code-Wiederverwendung | 100% (bestehende Vue SPA) | 0% (Neuimplementierung) |
| Feature-Parität iOS/Android | Strukturell garantiert | Daueraufgabe |
| Widget-Integration | Automatisch (Registry) | Manuell pro Plattform |
| Wartungsaufwand | Niedrig (ein Codebase) | Hoch (zwei oder drei) |
| Native Features | Via Plugins | Nativ, aber irrelevant hier |

Capacitor ist die einzig sinnvolle Wahl für diesen Anwendungsfall.

### Technologie-Stack

| Komponente | Technologie |
|---|---|
| Web Layer | Vue 3 Visu SPA (unverändert aus `frontend/`) |
| Native Shell | Capacitor 6 |
| iOS Minimum | iOS 16 |
| Android Minimum | Android 9 (API 28) |
| Build iOS | Xcode 15+, Apple Developer Account |
| Build Android | Android Studio / Gradle |
| CI/CD | GitHub Actions + Fastlane |

---

## 3. Monorepo-Struktur

```
obs/
├── backend/                        # Python/FastAPI (unverändert)
├── frontend/                       # Vue 3 Visu SPA (unverändert)
├── mobile/                         # NEU
│   ├── capacitor.config.ts         # Capacitor-Konfiguration
│   ├── package.json
│   ├── ios/                        # Xcode-Projekt (generiert)
│   ├── android/                    # Android-Projekt (generiert)
│   └── src/
│       ├── server-config/          # Einziger nativer Screen
│       │   ├── ServerConfig.vue
│       │   ├── CertificateImport.vue
│       │   └── ConnectionTest.vue
│       ├── plugins/                # Capacitor-Plugin-Wrapper
│       │   ├── secure-storage.ts   # Keychain/Keystore
│       │   ├── certificate.ts      # Client-Zertifikat-Handling
│       │   └── network.ts          # Verbindungstyp-Erkennung
│       └── mobile-overrides.css    # Mobile-spezifische CSS-Anpassungen
├── gateway/                        # NEU (eigene Spec: GATEWAY_SPEC.md)
└── docker-compose.yml
```

---

## 4. Capacitor-Konfiguration

```typescript
// mobile/capacitor.config.ts
import { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'ch.obs.visu',
  appName: 'OBS Visu',
  webDir: '../frontend/dist',        // Visu SPA Build-Output
  server: {
    // Im Dev-Modus: Live-Reload auf lokalen Vite-Dev-Server
    // Im Prod-Modus: auskommentiert → lokale Bundle-Files
    // url: 'http://192.168.1.x:5173',
    cleartext: true,                 // Erlaubt HTTP für lokale OBS-Instanzen
  },
  ios: {
    contentInset: 'always',
    allowsLinkPreview: false,
  },
  android: {
    allowMixedContent: true,         // HTTP-Verbindungen im LAN erlaubt
    captureInput: true,
  },
  plugins: {
    SplashScreen: {
      launchAutoHide: false,         // Manuell verstecken nach App-Init
      backgroundColor: '#1a1a1a',
    },
  },
};

export default config;
```

---

## 5. Server-Verbindungskonfiguration

Der einzige native Screen der App. Er erscheint beim ersten App-Start und ist danach über ein Einstellungs-Icon erreichbar.

### Datenmodell

```typescript
// Gespeichert im nativen Secure Storage (Keychain / Keystore)
interface ServerConnection {
  id: string                        // UUID, lokal generiert
  name: string                      // Anzeigename, z.B. "Zuhause"
  type: 'direct' | 'proxy' | 'gateway'
  url: string                       // https://obs.local:8080 oder https://obs.example.com
  gatewayToken?: string             // Nur bei type='gateway'
  clientCertificate?: {
    alias: string
    installedAt: Date
  }
  lastConnected?: Date
}
```

### Verbindungstypen im UI

```
┌─────────────────────────────────────────────┐
│  Server hinzufügen                          │
├─────────────────────────────────────────────┤
│  Name: [Zuhause                           ] │
│                                             │
│  Verbindungstyp:                            │
│  ○ Direkt (Lokal / VPN)                     │
│  ○ Extern (Reverse Proxy)                   │
│  ○ Cloud Gateway                            │
│                                             │
│  Server-URL: [https://                    ] │
│                                             │
│  [Client-Zertifikat importieren]  (optional)│
│                                             │
│  [Verbindung testen]                        │
│  [Speichern]                                │
└─────────────────────────────────────────────┘
```

### Zertifikat-Handling (Verbindungstyp "Extern")

Für Reverse-Proxy-Setups mit Mutual TLS (mTLS):

```typescript
// mobile/src/plugins/certificate.ts
import { Filesystem, Directory } from '@capacitor/filesystem';
import { SecureStorage } from '@capacitor-community/secure-storage';

export async function importClientCertificate(
  p12Data: Uint8Array,
  password: string,
  alias: string
): Promise<void> {
  // iOS: Via Capacitor Plugin in den iOS Keychain
  // Android: Via Capacitor Plugin in den Android KeyStore
  // Das P12-File selbst wird nach Import nicht persistent gespeichert
  await SecureStorage.set({
    key: `cert_${alias}`,
    value: btoa(String.fromCharCode(...p12Data))
  });
}
```

Der Benutzer importiert das `.p12`-Zertifikat aus dem Files-App (iOS) oder dem Datei-Manager (Android). Die App extrahiert es, speichert es im nativen Secure Storage und löscht das temporäre File.

---

## 6. Visu-Integration

### App-Start-Flow

```
App startet
  → Splash Screen
  → Gespeicherte Verbindungen laden (Secure Storage)
  → Keine Verbindung konfiguriert?
      → ServerConfig Screen (onboarding)
  → Verbindung konfiguriert?
      → Letzte Verbindung auswählen / User wählt
      → WebView laden mit Ziel-URL: {serverUrl}/visu/tree
      → Splash Screen ausblenden
```

### WebView-Konfiguration

Die Visu-SPA läuft in einem Capacitor WebView. Die App injiziert beim Start eine minimale Konfiguration:

```typescript
// Injiziert als window.__OBS_MOBILE_CONFIG__
interface ObsMobileConfig {
  serverUrl: string           // Aktive Server-URL
  connectionType: string      // 'direct' | 'proxy' | 'gateway'
  appVersion: string
  platform: 'ios' | 'android'
}
```

Die Visu-SPA liest `window.__OBS_MOBILE_CONFIG__` und überschreibt damit die Standard-API-Base-URL. Im Browser-Betrieb ist dieses Objekt nicht vorhanden — die SPA verhält sich wie gewohnt.

```typescript
// frontend/src/api/client.ts (Erweiterung)
const mobileConfig = (window as any).__OBS_MOBILE_CONFIG__;
export const API_BASE = mobileConfig?.serverUrl ?? '';
```

### Mobile CSS-Overrides

```css
/* mobile/src/mobile-overrides.css */
/* Injiziert nur im Capacitor-Kontext */

/* Safe Area für iPhone Notch / Dynamic Island */
:root {
  --safe-area-top: env(safe-area-inset-top);
  --safe-area-bottom: env(safe-area-inset-bottom);
}

body {
  padding-top: var(--safe-area-top);
  padding-bottom: var(--safe-area-bottom);
}

/* Touch-Targets vergrössern */
.widget-toggle button,
.widget-slider input[type="range"] {
  min-height: 44px;
  min-width: 44px;
}

/* Breadcrumb scrollbar auf Mobile */
.breadcrumb {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}

/* Editor auf Mobile deaktivieren (nur Viewer) */
.editor-fab,
.widget-palette {
  display: none !important;
}
```

> **Hinweis:** Der Drag & Drop Editor ist in der mobilen App nicht verfügbar. Die App ist ein reiner Viewer. Der Editor bleibt dem Browser vorbehalten.

---

## 7. Widget-System: keine Änderungen erforderlich

Neue Widgets, die in die `WidgetRegistry` registriert werden, erscheinen automatisch in der mobilen App. Es gibt keinen separaten Registrierungs- oder Build-Prozess für Mobile.

```typescript
// Beispiel: Ein neues Widget wird registriert
WidgetRegistry.register({
  type: 'Gauge',
  label: 'Rundinstrument',
  // ...
})
// → Sofort verfügbar im Browser UND in der Mobile App
```

---

## 8. Build-Prozess

### Development

```bash
# 1. Visu SPA bauen
cd frontend && npm run build

# 2. Capacitor sync (kopiert dist/ in iOS/Android Projekte)
cd ../mobile && npx cap sync

# 3a. iOS: Xcode öffnen
npx cap open ios

# 3b. Android: Android Studio öffnen
npx cap open android
```

### CI/CD (GitHub Actions)

```yaml
# .github/workflows/mobile-release.yml (Kurzfassung)
jobs:
  build-ios:
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4
      - run: cd frontend && npm ci && npm run build
      - run: cd mobile && npm ci && npx cap sync ios
      - uses: apple-actions/import-codesign-certs@v2
      - run: cd mobile/ios && fastlane release

  build-android:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cd frontend && npm ci && npm run build
      - run: cd mobile && npm ci && npx cap sync android
      - run: cd mobile/android && ./gradlew bundleRelease
      - uses: r0adkll/sign-android-release@v1
```

---

## 9. Sicherheitsüberlegungen

| Thema | Massnahme |
|---|---|
| Server-URL und Token | Nativer Secure Storage (Keychain / Keystore), nie im JS-Realm |
| Client-Zertifikate | Nativer Secure Storage, P12-Datei nach Import löschen |
| HTTP im LAN | Explizit erlaubt via `allowMixedContent` / `cleartext` für lokale IPs |
| HTTPS extern | Erzwungen für `proxy`- und `gateway`-Verbindungstypen |
| WebView | `allowsLinkPreview: false`, keine externen Navigationen |
| Kein Editor in App | CSS-Override verhindert Editor-Zugriff |

---

## 10. Implementierungsreihenfolge

### Phase M1 — Grundgerüst
1. Capacitor in Monorepo aufsetzen (`mobile/`)
2. iOS- und Android-Build-Targets einrichten
3. `ServerConfig.vue` — URL-Eingabe und lokale Speicherung
4. Verbindungstyp "Direkt" funktionsfähig → App lädt Visu via WebView
5. `window.__OBS_MOBILE_CONFIG__` Injektion
6. Safe-Area-CSS, mobile-overrides.css

### Phase M2 — Verbindungstypen vervollständigen
7. Zertifikat-Import und Secure Storage Plugin
8. Verbindungstyp "Extern" (Proxy mit optionalem mTLS)
9. Verbindungstyp "Gateway" (abhängig von Gateway-Implementierung)
10. Mehrere Server-Verbindungen verwalten

### Phase M3 — Polish & Release
11. Splash Screen, App-Icon, Launch Screen
12. CI/CD Pipeline (GitHub Actions + Fastlane)
13. TestFlight (iOS) / Internal Testing (Android)
14. App-Store-Submission

---

## 11. Abgrenzung

- **Kein** Editor in der App — nur Viewer
- **Keine** OBS-Konfiguration — nur Visu
- **Keine** Push-Notifications in Phase 1
- **Kein** Offline-Modus — App benötigt Verbindung zum OBS-Server
- **Keine** eigene Authentifizierung — nutzt JWT/PIN aus OBS-Backend
