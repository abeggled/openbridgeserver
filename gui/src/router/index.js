import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/login', name: 'Login',       component: () => import('@/views/LoginView.vue'),       meta: { public: true } },
  { path: '/',      name: 'Dashboard',   component: () => import('@/views/DashboardView.vue')    },
  { path: '/datapoints',           name: 'DataPoints', component: () => import('@/views/DataPointsView.vue') },
  { path: '/datapoints/:id',       name: 'DataPointDetail', component: () => import('@/views/DataPointDetailView.vue'), props: true },
  { path: '/adapters',             name: 'Adapters',   component: () => import('@/views/AdaptersView.vue')   },
  { path: '/history',              name: 'History',    component: () => import('@/views/HistoryView.vue')    },
  { path: '/ringbuffer',           name: 'RingBuffer', component: () => import('@/views/RingBufferView.vue') },
  { path: '/settings',             name: 'Settings',   component: () => import('@/views/SettingsView.vue')   },
  { path: '/logic',                name: 'Logic',      component: () => import('@/views/LogicView.vue')      },
{ path: '/:pathMatch(.*)*',      redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Routes accessible to the demo user (read-only mode)
const DEMO_ALLOWED = new Set(['Dashboard', 'Adapters', 'Settings', 'Login'])

function usernameFromToken() {
  const token = localStorage.getItem('access_token')
  if (!token) return null
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return payload.sub ?? null
  } catch { return null }
}

// Auth guard
router.beforeEach((to) => {
  const token = localStorage.getItem('access_token')
  if (!to.meta.public && !token) return { name: 'Login' }
  if (to.name === 'Login' && token)  return { name: 'Dashboard' }
  if (token && usernameFromToken() === 'demo' && !DEMO_ALLOWED.has(to.name)) {
    return { name: 'Dashboard' }
  }
})

export default router
