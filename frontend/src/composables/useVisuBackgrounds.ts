import { computed, ref } from 'vue'
import { visuBackgrounds } from '@/api/client'
import type { BackgroundOut } from '@/api/client'

const items = ref<BackgroundOut[]>([])
const loading = ref(false)
const error = ref('')
let listPromise: Promise<void> | null = null

export function useVisuBackgrounds() {
  function loadList(force = false): Promise<void> {
    if (!force && listPromise) return listPromise
    loading.value = true
    error.value = ''
    listPromise = visuBackgrounds
      .list()
      .then((data) => {
        items.value = data.backgrounds
      })
      .catch((err: unknown) => {
        items.value = []
        error.value = err instanceof Error ? err.message : ''
      })
      .finally(() => {
        loading.value = false
      })
    return listPromise
  }

  async function upload(files: FileList | File[]): Promise<void> {
    const arr = Array.from(files)
    if (arr.length === 0) return
    await visuBackgrounds.import(arr)
    await loadList(true)
  }

  async function remove(name: string): Promise<void> {
    await visuBackgrounds.delete([name])
    items.value = items.value.filter((bg) => bg.name !== name)
  }

  const names = computed(() => items.value.map((b) => b.name))

  return {
    items,
    names,
    loading,
    error,
    loadList,
    upload,
    remove,
  }
}
