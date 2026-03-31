/**
 * Vue Router — openTWS Visu
 *
 * Routen:
 *   /visu/tree          → Baumübersicht aller sichtbaren Knoten
 *   /visu/:id           → Viewer (PAGE oder LOCATION Auto-Übersicht)
 *   /visu/:id/auth      → PIN-Eingabe für protected-Knoten
 *   /editor/:id         → Drag & Drop Editor (JWT erforderlich)
 */

import { createRouter, createWebHistory } from 'vue-router'
import { getJwt } from '@/api/client'

const router = createRouter({
  history: createWebHistory('/visu/'),
  routes: [
    {
      path: '/',
      redirect: '/tree',
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
      path: '/:id',
      name: 'viewer',
      component: () => import('@/views/VisuViewer.vue'),
      props: true,
    },
    {
      path: '/editor/:id',
      name: 'editor',
      component: () => import('@/views/VisuEditor.vue'),
      props: true,
      meta: { requiresAuth: true },
    },
  ],
})

// ── Navigation Guard ──────────────────────────────────────────────────────────

router.beforeEach((to) => {
  if (to.meta.requiresAuth && !getJwt()) {
    // Editor erfordert JWT — zurück zur Baumübersicht
    return { name: 'tree' }
  }
})

// Globaler 401-Handler (ausgelöst vom API-Client)
window.addEventListener('visu:unauthorized', () => {
  router.push({ name: 'tree' })
})

export default router
