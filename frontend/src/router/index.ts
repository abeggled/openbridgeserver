/**
 * Vue Router — open bridge server Visu
 *
 * Routen:
 *   /visu/              → Startseite des Geräteprofils, sonst /tree
 *   /visu/tree          → Baumübersicht aller sichtbaren Knoten
 *   /visu/:id           → Viewer (PAGE oder LOCATION Auto-Übersicht)
 *   /visu/:id/auth      → PIN-Eingabe für protected-Knoten
 *   /editor/:id         → Drag & Drop Editor (JWT erforderlich)
 */

import { createRouter, createWebHistory } from 'vue-router'
import { getJwt, getIsAdmin } from '@/api/client'
import { useDeviceStore } from '@/stores/device'

const router = createRouter({
  history: createWebHistory('/visu/'),
  routes: [
    {
      // Leitet immer weiter: zur Startseite des Geräteprofils, sonst zur Übersicht.
      // Ein statisches `redirect` ginge nicht — es wird vor jedem Guard aufgelöst.
      path: '/',
      name: 'start',
      component: () => import('@/views/VisuTree.vue'),
      beforeEnter: async () => {
        const device = useDeviceStore()
        // Begrenzt gewartet: ein hängender Abruf darf die erste Navigation nicht
        // blockieren — dann öffnet sich die Übersicht, keepFresh() holt die Profile nach.
        await device.loadWithTimeout()
        return device.startRoute
      },
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
  // `?device=` darf an jeder Visu-URL hängen und merkt sich das Profil.
  useDeviceStore().applyQuery(to.query)
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
    // `replace` for the same reason as in AuthButton: after the login the page is
    // back in the history exactly once.
    router.replace({ name: 'login', query: { redirect: current.fullPath } })
  }
})

export default router
