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
  if (!a.running) return 'adapters.status.inactive'
  if (a.severity === 'error') return 'common.error'
  if (a.severity === 'warning') return 'adapters.status.degraded'
  if (a.connected) return 'adapters.status.connected'
  return 'adapters.status.running'
}

// Issue #779: the backend emits a stable status-detail `code` (+ params) under
// adapters.statusDetail.*; translate it here. Falls back to the non-localized
// `status_detail` string (dynamic/technical text such as raw exception output).
// `t`/`te` are passed in because this is a pure util without a Vue setup context;
// call it from a computed() so it stays reactive on locale change.
export function adapterStatusDetailText(a, t, te) {
  const code = a?.status_detail_code
  if (code) {
    const key = `adapters.statusDetail.${code}`
    if (te(key)) return t(key, a?.status_detail_params ?? {})
  }
  return a?.status_detail ?? ''
}
