import api from '@/api/client'

export const authzApi = {
  getUserGrants: (username) => api.get(`/authz/principals/user/${encodeURIComponent(username)}/grants`),
  updateUserGrants: (username, grants, etag) => api.put(
    `/authz/principals/user/${encodeURIComponent(username)}/grants`,
    { grants },
    { headers: { 'If-Match': etag } },
  ),
  preview: (data) => api.post('/authz/preview', data),
}
