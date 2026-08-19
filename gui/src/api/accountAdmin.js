import api from '@/api/client'

export const accountAdminApi = {
  userDeletionPreflight: (username) =>
    api.get(`/auth/users/${encodeURIComponent(username)}/deletion-preflight`),
  deleteUser: (username, data) =>
    api.delete(`/auth/users/${encodeURIComponent(username)}`, { data }),
  getApiKeyCapabilities: (id) =>
    api.get(`/auth/apikeys/${encodeURIComponent(id)}/capabilities`),
  replaceApiKeyCapabilities: (id, expected_revision, capabilities) =>
    api.put(`/auth/apikeys/${encodeURIComponent(id)}/capabilities`, { expected_revision, capabilities }),
}
