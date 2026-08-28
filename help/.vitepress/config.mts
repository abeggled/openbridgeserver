import { defineConfig } from 'vitepress'

// Served by FastAPI under /help (see obs/main.py) — analogous to gui_dist (/)
// and frontend_dist (/visu). Build output lives at ../help_dist, sibling to
// gui_dist/ and frontend_dist/, same as gui/ and frontend/ build to their
// own *_dist/ directories.
//
// German (`de`) is the Weblate source language for this repo (see
// docs/AGENT_REFERENCE.md, Internationalisation section) — it is the root
// locale here too. English lives under /en/.
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
    root: {
      label: 'Deutsch',
      lang: 'de',
      title: 'open bridge server Hilfe',
      description: 'Integriertes Hilfesystem für open bridge server',
      themeConfig: {
        nav: [{ text: 'Start', link: '/' }],
        sidebar: [
          {
            text: 'Erste Schritte',
            items: [{ text: 'Übersicht', link: '/' }],
          },
          {
            text: 'Dashboard',
            items: [{ text: 'Übersicht', link: '/dashboard/overview' }],
          },
          {
            text: 'Objekte',
            items: [{ text: 'Objektliste', link: '/datapoints/list' }],
          },
          {
            text: 'KNX-Geräte',
            items: [{ text: 'Geräteliste', link: '/knxdevices/list' }],
          },
          {
            text: 'Adapter',
            items: [{ text: 'Adapter-Instanzen', link: '/adapters/list' }],
          },
          {
            text: 'Historie',
            items: [{ text: 'Verlauf', link: '/history/overview' }],
          },
          {
            text: 'Monitor',
            items: [{ text: 'Monitor', link: '/ringbuffer/overview' }],
          },
          {
            text: 'Meldungsarchive',
            items: [{ text: 'Archivliste', link: '/messagearchives/list' }],
          },
          {
            text: 'Einstellungen',
            items: [
              { text: 'Allgemeine Einstellungen', link: '/settings/general' },
              { text: 'Passwort ändern', link: '/settings/password' },
              { text: 'Benutzer', link: '/settings/users' },
              { text: 'API Keys', link: '/settings/apikeys' },
              { text: 'Sicherheit', link: '/settings/security' },
              { text: 'Support', link: '/settings/support' },
              { text: 'Links', link: '/settings/links' },
              { text: 'Hierarchie', link: '/settings/hierarchy' },
              { text: 'Datenmanagement', link: '/settings/importexport' },
              { text: 'Icons', link: '/settings/icons' },
              { text: 'Historie DB', link: '/settings/history' },
              { text: 'Gefahrenzone', link: '/settings/dangerzone' },
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
