import { createRouter, createWebHistory } from 'vue-router'

// `meta.helpId` is the page-level entry point into the integrated help (#896):
// TopBar.vue renders the header help button from it, and tools/check_help_contract.py
// requires every named route to declare one (or to be listed, with a reason, in
// tools/help-contract-allowlist.txt) and to have it resolve in every locale.
const routes = [
  { path: '/login', name: 'Login',       component: () => import('@/views/LoginView.vue'),       meta: { public: true } },
  { path: '/',      name: 'Dashboard',   component: () => import('@/views/DashboardView.vue'),   meta: { helpId: 'dashboard' } },
  { path: '/datapoints',           name: 'DataPoints', component: () => import('@/views/DataPointsView.vue'), meta: { helpId: 'datapoints' } },
  { path: '/datapoints/:id',       name: 'DataPointDetail', component: () => import('@/views/DataPointDetailView.vue'), props: true, meta: { helpId: 'datapoints-detail' } },
  { path: '/adapters',             name: 'Adapters',   component: () => import('@/views/AdaptersView.vue'),   meta: { helpId: 'adapters' } },
  { path: '/knx-devices',          name: 'KnxDevices', component: () => import('@/views/KnxDevicesView.vue'), meta: { helpId: 'knxdevices' } },
  { path: '/history',              name: 'History',    component: () => import('@/views/HistoryView.vue'),    meta: { helpId: 'history' } },
  { path: '/ringbuffer',           name: 'RingBuffer', component: () => import('@/views/RingBufferView.vue'), meta: { helpId: 'ringbuffer' } },
  { path: '/message-archives',     name: 'MessageArchives', component: () => import('@/views/MessageArchivesView.vue'), meta: { helpId: 'messagearchives' } },
  { path: '/logs',                 name: 'Logs',       component: () => import('@/views/LogView.vue'),        meta: { helpId: 'logs' } },
  { path: '/settings',             name: 'Settings',   component: () => import('@/views/SettingsView.vue'),   meta: { helpId: 'settings' } },
  { path: '/logic',                name: 'Logic',      component: () => import('@/views/LogicView.vue'),      meta: { helpId: 'logic' } },
{ path: '/:pathMatch(.*)*',      redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Auth guard
router.beforeEach((to) => {
  const token = localStorage.getItem('access_token')
  if (!to.meta.public && !token) return { name: 'Login' }
  if (to.name === 'Login' && token)  return { name: 'Dashboard' }
})

export default router
