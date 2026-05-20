/**
 * open bridge server API Client
 * All calls go through /api/v1 — in dev proxied via Vite, in prod served by FastAPI.
 */
import axios from 'axios'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

// ── Request: inject JWT ───────────────────────────────────────────────────
api.interceptors.request.use(config => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// ── Response: auto-refresh or redirect on 401 ────────────────────────────
api.interceptors.response.use(
  res => res,
  async err => {
    const original = err.config
    if (err.response?.status === 401 && !original._retry) {
      original._retry = true
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken) {
        try {
          const { data } = await axios.post('/api/v1/auth/refresh', { refresh_token: refreshToken })
          localStorage.setItem('access_token', data.access_token)
          localStorage.setItem('refresh_token', data.refresh_token)
          original.headers.Authorization = `Bearer ${data.access_token}`
          return api(original)
        } catch {
          // Refresh failed — clear storage and redirect
        }
      }
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default api

// ── Auth ─────────────────────────────────────────────────────────────────
export const authApi = {
  login:          (username, password) => api.post('/auth/login', { username, password }),
  me:             ()                   => api.get('/auth/me'),
  changePassword: (current_password, new_password) =>
                    api.post('/auth/me/change-password', { current_password, new_password }),
  listUsers:      ()                   => api.get('/auth/users'),
  createUser:     (data)               => api.post('/auth/users', data),
  updateUser:     (username, data)     => api.patch(`/auth/users/${username}`, data),
  deleteUser:     (username)           => api.delete(`/auth/users/${username}`),
  setMqttPassword:    (username, password) => api.post(`/auth/users/${username}/mqtt-password`, { password }),
  deleteMqttPassword: (username)           => api.delete(`/auth/users/${username}/mqtt-password`),
  listApiKeys:    ()                   => api.get('/auth/apikeys'),
  createApiKey:   (name)               => api.post('/auth/apikeys', { name }),
  deleteApiKey:   (id)                 => api.delete(`/auth/apikeys/${id}`),
}

// ── DataPoints ────────────────────────────────────────────────────────────
export const dpApi = {
  list:          (page = 0, size = 50, sort = 'created_at', order = 'asc') => api.get('/datapoints/', { params: { page, size, sort, order } }),
  listAll: async () => {
    const size = 500
    const first = await api.get('/datapoints/', { params: { page: 0, size, sort: 'name', order: 'asc' } })
    const { items, pages } = first.data
    if (pages <= 1) return { data: { items } }
    const rest = await Promise.all(
      Array.from({ length: pages - 1 }, (_, i) =>
        api.get('/datapoints/', { params: { page: i + 1, size, sort: 'name', order: 'asc' } })
      )
    )
    return { data: { items: [...items, ...rest.flatMap(r => r.data.items)] } }
  },
  get:           (id)                           => api.get(`/datapoints/${id}`),
  create:        (data)                         => api.post('/datapoints/', data),
  update:        (id, data)                     => api.patch(`/datapoints/${id}`, data),
  delete:        (id)                           => api.delete(`/datapoints/${id}`),
  value:         (id)                           => api.get(`/datapoints/${id}/value`),
  writeValue:    (id, value)                    => api.post(`/datapoints/${id}/value`, { value }),
  tags:          ()                             => api.get('/datapoints/tags'),
  listBindings:  (id)                           => api.get(`/datapoints/${id}/bindings`),
  createBinding: (id, data)                     => api.post(`/datapoints/${id}/bindings`, data),
  updateBinding: (id, bindingId, data)          => api.patch(`/datapoints/${id}/bindings/${bindingId}`, data),
  deleteBinding: (id, bindingId)                => api.delete(`/datapoints/${id}/bindings/${bindingId}`),
}

// ── Search ────────────────────────────────────────────────────────────────
export const searchApi = {
  search: (params) => api.get('/search/', { params }),
}

// ── Adapters ──────────────────────────────────────────────────────────────
export const adapterApi = {
  // Typ-Routen (Schema-Abfragen)
  list:         ()                           => api.get('/adapters/'),
  schema:       (type)                       => api.get(`/adapters/${type}/schema`),
  bindingSchema:(type)                       => api.get(`/adapters/${type}/binding-schema`),
  knxDpts:      ()                           => api.get('/adapters/knx/dpts'),
  test:         (type, config)               => api.post(`/adapters/${type}/test`, { config }),
  getConfig:    (type)                       => api.get(`/adapters/${type}/config`),
  updateConfig: (type, config, enabled=true) => api.patch(`/adapters/${type}/config`, { config, enabled }),

  // Instanz-Routen (Multi-Instance, Phase 5)
  listInstances:   ()           => api.get('/adapters/instances'),
  createInstance:  (data)       => api.post('/adapters/instances', data),
  getInstance:     (id)         => api.get(`/adapters/instances/${id}`),
  updateInstance:  (id, data)   => api.patch(`/adapters/instances/${id}`, data),
  deleteInstance:  (id)         => api.delete(`/adapters/instances/${id}`),
  testInstance:       (id, config)      => api.post(`/adapters/instances/${id}/test`, { config }),
  restartInstance:    (id)              => api.post(`/adapters/instances/${id}/restart`),
  mqttBrowseTopics:   (id, timeout = 5) => api.get(`/adapters/instances/${id}/mqtt/browse`, { params: { timeout }, timeout: (timeout + 3) * 1000 }),
  mqttSamplePayload:  (id, topic, timeout = 5) => api.get(`/adapters/instances/${id}/mqtt/sample`, { params: { topic, timeout }, timeout: (timeout + 3) * 1000 }),
  iobrokerBrowseStates: (id, q = '', limit = 50) => api.get(`/adapters/instances/${id}/iobroker/states`, { params: { q, limit } }),
  iobrokerImportPreview: (id, data) => api.post(`/adapters/instances/${id}/iobroker/import-preview`, data),
  iobrokerImport:        (id, data) => api.post(`/adapters/instances/${id}/iobroker/import`, data),
  getZsuHolidays:        (id, year = 0) => api.get(`/adapters/instances/${id}/holidays`, { params: year ? { year } : {} }),
  anwesenheitDatapoints:  (id)          => api.get(`/adapters/instances/${id}/anwesenheit/datapoints`),
  anwesenheitSyncBindings:(id, dpIds)  => api.post(`/adapters/instances/${id}/anwesenheit/sync-bindings`, { datapoint_ids: dpIds }),
  anwesenheitHealth:      (id)          => api.get(`/adapters/instances/${id}/anwesenheit/health`),
  snmpWalk: (id, host, oid = '1.3.6.1.2.1', port = 161, maxResults = 50, timeout = 10, startOid = null) => {
    const params = { host, oid, port, max_results: maxResults, timeout }
    if (startOid) params.start_oid = startOid
    return api.get(`/adapters/instances/${id}/snmp/walk`, { params, timeout: (timeout + 5) * 1000 })
  },
}

// ── KNX Keyfile ───────────────────────────────────────────────────────────
export const knxKeyfileApi = {
  scan:   (params = {}) => api.get('/knx/scan', { params, timeout: ((params.timeout ?? 4) + 3) * 1000 }),
  upload: (formData)    => api.post('/knx/keyfile', formData, { headers: { 'Content-Type': 'multipart/form-data' } }),
  delete: (fileId)      => api.delete(`/knx/keyfile/${fileId}`),
}

// ── KNX Project Import ────────────────────────────────────────────────────
export const knxprojApi = {
  import:  (formData, params = {}) => api.post('/knxproj/import', formData, { headers: { 'Content-Type': 'multipart/form-data' }, params }),
  listGA:  (params)   => api.get('/knxproj/group-addresses', { params }),
  clearGA: ()         => api.delete('/knxproj/group-addresses'),
}

// ── Hierarchy Manager ─────────────────────────────────────────────────────
export const hierarchyApi = {
  // Trees
  listTrees:    ()              => api.get('/hierarchy/trees'),
  createTree:   (data)          => api.post('/hierarchy/trees', data),
  updateTree:   (id, data)      => api.put(`/hierarchy/trees/${id}`, data),
  deleteTree:   (id)            => api.delete(`/hierarchy/trees/${id}`),

  // Nodes
  getTreeNodes: (treeId)        => api.get(`/hierarchy/trees/${treeId}/nodes`),
  createNode:   (data)          => api.post('/hierarchy/nodes', data),
  updateNode:   (id, data)      => api.put(`/hierarchy/nodes/${id}`, data),
  moveNode:     (id, data)      => api.put(`/hierarchy/nodes/${id}/move`, data),
  deleteNode:   (id)            => api.delete(`/hierarchy/nodes/${id}`),

  // Links
  getNodeDatapoints:  (nodeId)  => api.get(`/hierarchy/nodes/${nodeId}/datapoints`),
  getDatapointNodes:  (dpId)    => api.get(`/hierarchy/datapoints/${dpId}/nodes`),
  createLink:   (data)          => api.post('/hierarchy/links', data),
  deleteLink:   (nodeId, dpId)  => api.delete('/hierarchy/links', { params: { node_id: nodeId, datapoint_id: dpId } }),

  // Node search (for DP detail view)
  searchNodes:   (q = '', limit = 30) => api.get('/hierarchy/nodes/search', { params: { q, limit } }),

  // ETS import
  importFromEts: (data)         => api.post('/hierarchy/import-from-ets', data),
}

// ── System ────────────────────────────────────────────────────────────────
export const systemApi = {
  health:    () => axios.get('/api/v1/system/health'),  // no auth
  adapters:  () => api.get('/system/adapters'),
  datatypes: () => api.get('/system/datatypes'),
}

// ── App Settings ──────────────────────────────────────────────────────────
export const settingsApi = {
  get:    ()     => api.get('/system/settings'),
  update: (data) => api.put('/system/settings', data),
}

// ── History Settings ───────────────────────────────────────────────────────
export const historySettingsApi = {
  get:    ()     => api.get('/system/history/settings'),
  update: (data) => api.put('/system/history/settings', data),
  test:   (data) => api.post('/system/history/test', data),
}

// ── History ───────────────────────────────────────────────────────────────
export const historyApi = {
  query:     (id, params) => api.get(`/history/${id}`, { params }),
  aggregate: (id, params) => api.get(`/history/${id}/aggregate`, { params }),
}

// ── Log Buffer ────────────────────────────────────────────────────────────
export const logsApi = {
  list:     (params) => api.get('/system/logs', { params }),
  getLevel: ()       => api.get('/system/log-level'),
  setLevel: (level)  => api.put('/system/log-level', { level }),
}

// ── RingBuffer ────────────────────────────────────────────────────────────
export const ringbufferApi = {
  query:  (params)                  => api.get('/ringbuffer/', { params }),
  queryV2:(body)                    => api.post('/ringbuffer/query', body),
  exportCsv: (body)                 => api.post('/ringbuffer/export/csv', body, { responseType: 'blob' }),
  stats:  ()                        => api.get('/ringbuffer/stats'),
  config: (body)                    => api.post('/ringbuffer/config', body),
  listFiltersets: ()                => api.get('/ringbuffer/filtersets'),
  getFilterset: (id)                => api.get(`/ringbuffer/filtersets/${id}`),
  createFilterset: (body)           => api.post('/ringbuffer/filtersets', body),
  updateFilterset: (id, body)       => api.put(`/ringbuffer/filtersets/${id}`, body),
  deleteFilterset: (id)             => api.delete(`/ringbuffer/filtersets/${id}`),
  cloneFilterset: (id, name = null) => api.post(`/ringbuffer/filtersets/${id}/clone`, name ? { name } : {}),
  queryFilterset: (id)              => api.post(`/ringbuffer/filtersets/${id}/query`, {}),
  // #431 — flat filterset schema, multi-active topbar
  patchFiltersetTopbar: (id, body)  => api.patch(`/ringbuffer/filtersets/${id}/topbar`, body),
  patchFiltersetOrder: (items)      => api.patch('/ringbuffer/filtersets/order', { items }),
  queryMultiFiltersets: (body)      => api.post('/ringbuffer/filtersets/query', body),
  // #427 — multi-set CSV/TSV export + persisted format defaults
  exportMultiCsv: (body)            => api.post('/ringbuffer/filtersets/export/csv', body, { responseType: 'blob' }),
  countExportRows: (body)           => api.post('/ringbuffer/filtersets/export/count', body),
  getExportSettings: ()             => api.get('/ringbuffer/export/settings'),
  putExportSettings: (body)         => api.put('/ringbuffer/export/settings', body),
}

// ── Config Import/Export ──────────────────────────────────────────────────
export const configApi = {
  export:          ()     => api.get('/config/export'),
  exportDb:        ()     => api.get('/config/export/db', { responseType: 'blob' }),
  import:          (data) => api.post('/config/import', data),
  importDb:        (file) => {
    const fd = new FormData(); fd.append('file', file)
    return api.post('/config/import/db', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  reset:           ()     => api.delete('/config/reset'),
  resetBindings:   ()     => api.delete('/config/reset/bindings'),
  resetDatapoints: ()     => api.delete('/config/reset/datapoints'),
  resetLogic:      ()     => api.delete('/config/reset/logic'),
  resetAdapters:   ()     => api.delete('/config/reset/adapters'),
}

// ── Autobackup ────────────────────────────────────────────────────────────
export const autobackupApi = {
  getConfig:    ()           => api.get('/config/autobackup/config'),
  setConfig:    (cfg)        => api.put('/config/autobackup/config', cfg),
  list:         ()           => api.get('/config/autobackup/list'),
  runNow:       ()           => api.post('/config/autobackup/run'),
  restore:      (name)       => api.post(`/config/autobackup/restore/${name}`),
  delete:       (name)       => api.delete(`/config/autobackup/${name}`),
}

// ── Icons Library ─────────────────────────────────────────────────────────
export const iconsApi = {
  list:           ()                       => api.get('/icons/'),
  import:         (formData)               => api.post('/icons/import', formData, { headers: { 'Content-Type': 'multipart/form-data' } }),
  get:            (name)                   => api.get(`/icons/${name}`, { responseType: 'text' }),
  delete:         (names)                  => api.delete('/icons/', { data: { names } }),
  export:         (names = [])             => api.post('/icons/export', { names }, { responseType: 'blob' }),
  importFa:       (data)                   => api.post('/icons/fontawesome', data),
  getSettings:    ()                       => api.get('/icons/settings'),
  saveSettings:   (data)                   => api.put('/icons/settings', data),
}

// ── Nav Links ─────────────────────────────────────────────────────────────
export const navLinksApi = {
  list:   ()          => api.get('/system/nav-links'),
  create: (data)      => api.post('/system/nav-links', data),
  update: (id, data)  => api.patch(`/system/nav-links/${id}`, data),
  delete: (id)        => api.delete(`/system/nav-links/${id}`),
}

// ── Logic Engine ──────────────────────────────────────────────────────────
export const logicApi = {
  nodeTypes:        ()           => api.get('/logic/node-types'),
  listGraphs:       ()           => api.get('/logic/graphs'),
  createGraph:      (data)       => api.post('/logic/graphs', data),
  importGraph:      (data)       => api.post('/logic/graphs/import', data),
  getGraph:         (id)         => api.get(`/logic/graphs/${id}`),
  saveGraph:        (id, data)   => api.put(`/logic/graphs/${id}`, data),
  patchGraph:       (id, data)   => api.patch(`/logic/graphs/${id}`, data),
  deleteGraph:      (id)         => api.delete(`/logic/graphs/${id}`),
  runGraph:         (id)         => api.post(`/logic/graphs/${id}/run`),
  duplicateGraph:   (id)         => api.post(`/logic/graphs/${id}/duplicate`),
  exportGraph:      (id)         => api.get(`/logic/graphs/${id}/export`),
  datapointUsages:  (dpId)       => api.get(`/logic/datapoint/${dpId}/usages`),
}
