// Pure helpers that map an AdapterInstanceOut to its visible status — used by
// AdaptersView, DashboardView and any future place that renders an adapter row.
// Issue #466: ordering matters — severity overrides connected so a "warning"
// adapter is rendered amber even while its tunnel is technically up.

export function adapterDotClass(a) {
  if (!a.running) return 'bg-slate-600'
  if (a.severity === 'error') return 'bg-red-500'
  if (a.severity === 'warning') return 'bg-amber-400'
  if (a.connected) return 'bg-green-400'
  return 'bg-amber-400 animate-pulse'
}

export function adapterBadgeVariant(a) {
  if (!a.running) return 'muted'
  if (a.severity === 'error') return 'danger'
  if (a.severity === 'warning') return 'warning'
  if (a.connected) return 'success'
  return 'warning'
}

export function adapterStatusLabel(a) {
  if (!a.running) return 'Inaktiv'
  if (a.severity === 'error') return 'Fehler'
  if (a.severity === 'warning') return 'Eingeschränkt'
  if (a.connected) return 'Verbunden'
  return 'Läuft'
}
