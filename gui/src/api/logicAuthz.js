import api from '@/api/client'

export const logicRunAuthzApi = {
  preflight: (graphId) => api.get(`/logic/graphs/${graphId}/run-preflight`),
}
