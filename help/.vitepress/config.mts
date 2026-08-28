import { defineConfig } from 'vitepress'

// Served by FastAPI under /help (see obs/main.py) — analogous to gui_dist (/)
// and frontend_dist (/visu). Build output lives at ../help_dist, sibling to
// gui_dist/ and frontend_dist/, same as gui/ and frontend/ build to their
// own *_dist/ directories.
//
// Every locale, including German, lives under its own prefixed path
// (/de/, /en/, ...) — there is no unprefixed "root" locale. This mirrors
// gui/frontend's Weblate setup, where English is the translation source and
// German is a normal (if usually already-complete) target language, not the
// source (see docs/AGENT_REFERENCE.md, Internationalisation section). The
// backend redirects the bare /help/ to /help/de/ (see obs/main.py) since
// VitePress does not do that on its own for a root-less locale config.
export default defineConfig({
  base: '/help/',
  outDir: '../help_dist',
  title: 'open bridge server Hilfe',

  // The Admin-GUI (HelpDrawer.vue) appends ?appearance=dark|light to the
  // iframe src to carry its own current dark/light state across the
  // same-origin-but-independent iframe document. This inline script reads
  // that param and seeds VitePress's own localStorage key *before*
  // VitePress's built-in anti-FOUC "check-dark-mode" script runs (that
  // script is appended by VitePress itself, always after any user-supplied
  // `head` entries — see resolveSiteDataHead() in vitepress/dist/node) — so
  // the very first paint already matches, no flash of the wrong theme.
  // Without this, the iframe falls back to its own prefers-color-scheme
  // detection, which can silently disagree with the Admin-GUI's theme.
  head: [
    ['script', {}, `(function(){
      var m = location.search.match(/[?&]appearance=(dark|light)/);
      if (m) localStorage.setItem('vitepress-theme-appearance', m[1]);
    })();`],
  ],

  locales: {
    de: {
      label: 'Deutsch',
      lang: 'de',
      link: '/de/',
      title: 'open bridge server Hilfe',
      description: 'Integriertes Hilfesystem für open bridge server',
      themeConfig: {
        nav: [{ text: 'Start', link: '/de/' }],
        sidebar: [
          {
            text: 'Erste Schritte',
            items: [{ text: 'Übersicht', link: '/de/' }],
          },
          {
            text: 'Dashboard',
            items: [{ text: 'Übersicht', link: '/de/dashboard/overview' }],
          },
          {
            text: 'Objekte',
            items: [{ text: 'Objektliste', link: '/de/datapoints/list' }],
          },
          {
            text: 'KNX-Geräte',
            items: [{ text: 'Geräteliste', link: '/de/knxdevices/list' }],
          },
          {
            text: 'Adapter',
            items: [{ text: 'Adapter-Instanzen', link: '/de/adapters/list' }],
          },
          {
            text: 'Historie',
            items: [{ text: 'Verlauf', link: '/de/history/overview' }],
          },
          {
            text: 'Monitor',
            items: [{ text: 'Monitor', link: '/de/ringbuffer/overview' }],
          },
          {
            text: 'Meldungsarchive',
            items: [{ text: 'Archivliste', link: '/de/messagearchives/list' }],
          },
          {
            text: 'Logs',
            items: [{ text: 'Logs', link: '/de/logs/overview' }],
          },
          {
            text: 'Logikmodul',
            items: [
              { text: 'Logikmodul', link: '/de/logic/overview' },
              { text: 'Bausteine: Logik', link: '/de/logic/blocks-logic' },
              { text: 'Bausteine: Objekt-Zugriff', link: '/de/logic/blocks-datapoint' },
              { text: 'Bausteine: Mathematik', link: '/de/logic/blocks-math' },
              { text: 'Bausteine: Text', link: '/de/logic/blocks-string' },
              { text: 'Bausteine: Zeit', link: '/de/logic/blocks-timer' },
              { text: 'Bausteine: Astro', link: '/de/logic/blocks-astro' },
              { text: 'Bausteine: Benachrichtigung', link: '/de/logic/blocks-notification' },
              { text: 'Bausteine: Integration', link: '/de/logic/blocks-integration' },
              { text: 'Bausteine: Skript', link: '/de/logic/blocks-script' },
              { text: 'Bausteine: KI', link: '/de/logic/blocks-ai' },
            ],
          },
          {
            text: 'Einstellungen',
            items: [
              { text: 'Allgemeine Einstellungen', link: '/de/settings/general' },
              { text: 'Passwort ändern', link: '/de/settings/password' },
              { text: 'Benutzer', link: '/de/settings/users' },
              { text: 'API Keys', link: '/de/settings/apikeys' },
              { text: 'Sicherheit', link: '/de/settings/security' },
              { text: 'Support', link: '/de/settings/support' },
              { text: 'Links', link: '/de/settings/links' },
              { text: 'Hierarchie', link: '/de/settings/hierarchy' },
              { text: 'Datenmanagement', link: '/de/settings/importexport' },
              { text: 'Icons', link: '/de/settings/icons' },
              { text: 'Historie DB', link: '/de/settings/history' },
              { text: 'Gefahrenzone', link: '/de/settings/dangerzone' },
            ],
          },
        ],
        outline: { label: 'Auf dieser Seite' },
        docFooter: { prev: 'Vorherige Seite', next: 'Nächste Seite' },
        darkModeSwitchLabel: 'Darstellung',
        returnToTopLabel: 'Nach oben',
      },
    },
    en: {
      label: 'English',
      lang: 'en',
      link: '/en/',
      title: 'open bridge server Help',
      description: 'Integrated help system for open bridge server',
      themeConfig: {
        nav: [{ text: 'Home', link: '/en/' }],
        sidebar: [
          {
            text: 'Getting Started',
            items: [{ text: 'Overview', link: '/en/' }],
          },
          {
            text: 'Dashboard',
            items: [{ text: 'Overview', link: '/en/dashboard/overview' }],
          },
          {
            text: 'Data Points',
            items: [{ text: 'Data Point List', link: '/en/datapoints/list' }],
          },
          {
            text: 'KNX Devices',
            items: [{ text: 'Device List', link: '/en/knxdevices/list' }],
          },
          {
            text: 'Adapters',
            items: [{ text: 'Adapter Instances', link: '/en/adapters/list' }],
          },
          {
            text: 'History',
            items: [{ text: 'History', link: '/en/history/overview' }],
          },
          {
            text: 'Monitor',
            items: [{ text: 'Monitor', link: '/en/ringbuffer/overview' }],
          },
          {
            text: 'Message Archives',
            items: [{ text: 'Archive List', link: '/en/messagearchives/list' }],
          },
          {
            text: 'Logs',
            items: [{ text: 'Logs', link: '/en/logs/overview' }],
          },
          {
            text: 'Logic Module',
            items: [
              { text: 'Logic Module', link: '/en/logic/overview' },
              { text: 'Blocks: Logic', link: '/en/logic/blocks-logic' },
              { text: 'Blocks: Data Point Access', link: '/en/logic/blocks-datapoint' },
              { text: 'Blocks: Math', link: '/en/logic/blocks-math' },
              { text: 'Blocks: Text', link: '/en/logic/blocks-string' },
              { text: 'Blocks: Timer', link: '/en/logic/blocks-timer' },
              { text: 'Blocks: Astro', link: '/en/logic/blocks-astro' },
              { text: 'Blocks: Notification', link: '/en/logic/blocks-notification' },
              { text: 'Blocks: Integration', link: '/en/logic/blocks-integration' },
              { text: 'Blocks: Script', link: '/en/logic/blocks-script' },
              { text: 'Blocks: AI', link: '/en/logic/blocks-ai' },
            ],
          },
          {
            text: 'Settings',
            items: [
              { text: 'General Settings', link: '/en/settings/general' },
              { text: 'Change Password', link: '/en/settings/password' },
              { text: 'Users', link: '/en/settings/users' },
              { text: 'API Keys', link: '/en/settings/apikeys' },
              { text: 'Security', link: '/en/settings/security' },
              { text: 'Support', link: '/en/settings/support' },
              { text: 'Links', link: '/en/settings/links' },
              { text: 'Hierarchy', link: '/en/settings/hierarchy' },
              { text: 'Data Management', link: '/en/settings/importexport' },
              { text: 'Icons', link: '/en/settings/icons' },
              { text: 'History DB', link: '/en/settings/history' },
              { text: 'Danger Zone', link: '/en/settings/dangerzone' },
            ],
          },
        ],
      },
    },
  },

  themeConfig: {
    socialLinks: [
      { icon: 'github', link: 'https://github.com/abeggled/openbridgeserver' },
    ],
  },
})
