/**
 * Vue Router — open bridge server Visu
 *
 * Routen:
 *   /visu/tree          → Baumübersicht aller sichtbaren Knoten
 *   /visu/:id           → Viewer (PAGE oder LOCATION Auto-Übersicht)
 *   /visu/:id/auth      → PIN-Eingabe für protected-Knoten
 *   /editor/:id         → Drag & Drop Editor (JWT erforderlich)
 */

import { createRouter, createWebHistory } from 'vue-router'
import { getJwt, getIsAdmin } from '@/api/client'

const router = createRouter({
  history: createWebHistory('/visu/'),
  routes: [
    {
      path: '/',
      redirect: '/tree',
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
    },
    {
      path: '/tree',
      name: 'tree',
      component: () => import('@/views/VisuTree.vue'),
    },
    {
      path: '/:id/auth',
      name: 'pin-auth',
      component: () => import('@/views/PinAuth.vue'),
      props: true,
    },
    {
      path: '/manage',
      name: 'manage',
      component: () => import('@/views/TreeManager.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/editor/:id',
      name: 'editor',
      component: () => import('@/views/VisuEditor.vue'),
      props: true,
      meta: { requiresAuth: true },
    },
    // Viewer muss nach /editor/:id stehen (sonst matcht /:id zuerst)
    {
      path: '/:id',
      name: 'viewer',
      component: () => import('@/views/VisuViewer.vue'),
      props: true,
    },
  ],
})

// ── Navigation Guard ──────────────────────────────────────────────────────────

router.beforeEach((to) => {
  if (to.meta.requiresAuth && !getJwt()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAdmin && !getIsAdmin()) {
    // Eingeloggt aber kein Admin → zurück zur Übersicht
    return { name: 'tree' }
  }
})

// Globaler 401-Handler (ausgelöst vom API-Client)
// Nur weiterleiten wenn die aktuelle Route tatsächlich Authentifizierung erfordert
window.addEventListener('visu:unauthorized', () => {
  const current = router.currentRoute.value
  if (current.meta.requiresAuth) {
    router.push({ name: 'login', query: { redirect: current.fullPath } })
  }
})

export default router
