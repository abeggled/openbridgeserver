// Vitest setup: provide a tiny localStorage shim and stub for window.matchMedia
// so views that import API client (which inspects localStorage at module load
// for auth tokens) don't crash inside happy-dom.

if (typeof globalThis.matchMedia !== 'function') {
  globalThis.matchMedia = () => ({
    matches: false,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
  })
}
