import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export const useThemeStore = defineStore('theme', () => {
  const isDark = ref(
    localStorage.getItem('visu_theme') === 'dark' ||
    (localStorage.getItem('visu_theme') === null &&
      window.matchMedia('(prefers-color-scheme: dark)').matches)
  )

  watch(isDark, (dark) => {
    localStorage.setItem('visu_theme', dark ? 'dark' : 'light')
    document.documentElement.classList.toggle('dark', dark)
  }, { immediate: true })

  function toggle() {
    isDark.value = !isDark.value
  }

  return { isDark, toggle }
})
