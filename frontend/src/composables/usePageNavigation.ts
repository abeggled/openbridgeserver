import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import type { AccessLevel, VisuNode } from '@/types'
import { useVisuStore } from '@/stores/visu'

/**
 * Seitennavigation, wie sie ein antippbares Widget auslöst (Link, Kamera).
 *
 * - `targetId`: Zielseite oder -bereich. Bei einem Bereich wird die erste
 *   sichtbare Seite darin geöffnet, bei einem PIN-geschützten Bereich ohne
 *   Sitzung der Bereich selbst (dort fragt der Viewer die PIN ab).
 * - `historyBack`: zurück zur zuvor besuchten Seite, egal welche; `targetId`
 *   ist dann das Ersatzziel, wenn es keinen Verlauf gibt (Lesezeichen,
 *   Kiosk-Start, neuer Tab).
 * - `editorMode`: im Editor wird nie navigiert.
 */
export function usePageNavigation(options: {
  targetId: MaybeRefOrGetter<string>
  historyBack: MaybeRefOrGetter<boolean>
  editorMode: MaybeRefOrGetter<boolean>
}) {
  const router = useRouter()
  const route  = useRoute()
  const store  = useVisuStore()

  // history.state ist nicht reaktiv; es ändert sich mit jeder Navigation, also
  // wird die aktuelle Route mitgelesen. Im Editor sagt es nichts über den
  // Verlauf der Visu aus.
  const canGoBack = computed(() => {
    void route.fullPath
    return !toValue(options.editorMode) && !!window.history.state?.back
  })

  // Ohne Verlauf ist nur die Ersatzseite ein Ziel.
  const hasDestination = computed(() =>
    !!toValue(options.targetId) || (toValue(options.historyBack) && canGoBack.value),
  )

  function resolveAccessNode(node: VisuNode): { access: AccessLevel; definingId: string } {
    let cur: VisuNode | undefined = node
    while (cur) {
      if (cur.access !== null) return { access: cur.access, definingId: cur.id }
      cur = cur.parent_id ? store.getNode(cur.parent_id) : undefined
    }
    return { access: 'public', definingId: node.id }
  }

  function isVisibleInLocationOverview(node: VisuNode): boolean {
    const { access } = resolveAccessNode(node)
    if (access === 'user') return store.isLoggedIn
    return true
  }

  function navigate() {
    if (toValue(options.editorMode)) return
    if (toValue(options.historyBack) && window.history.state?.back) {
      router.back()
      return
    }
    const targetId = toValue(options.targetId)
    if (!targetId) return
    const target = store.getNode(targetId)
    if (target?.type === 'LOCATION') {
      const targetAccess = resolveAccessNode(target)
      if (targetAccess.access === 'protected' && !store.hasSessionToken(targetAccess.definingId)) {
        router.push({ name: 'viewer', params: { id: targetId } })
        return
      }

      // Erste direkte Seite, die auch in der Bereichsübersicht sichtbar wäre.
      const firstPage = store.getChildren(targetId)
        .find(n => n.type === 'PAGE' && isVisibleInLocationOverview(n))
      if (firstPage) {
        router.push({ name: 'viewer', params: { id: firstPage.id } })
        return
      }
    }
    router.push({ name: 'viewer', params: { id: targetId } })
  }

  return { navigate, canGoBack, hasDestination }
}
