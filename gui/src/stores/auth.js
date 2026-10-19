import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const user    = ref(null)   // { id, username, is_admin, created_at }
  const loading = ref(false)
  const error   = ref(null)

  // localStorage is not reactive: without this the first value of isLoggedIn
  // (false on the login page) stays cached after an in-app login, and e.g. the
  // update notice mounted afterwards never polls. Login and logout bump it; the
  // API client's token refresh keeps the login, its failure reloads the page.
  const tokenRevision = ref(0)
  const isLoggedIn  = computed(() => {
    void tokenRevision.value  // dependency only
    return !!localStorage.getItem('access_token')
  })
  const isAdmin     = computed(() => user.value?.is_admin ?? false)
  const username    = computed(() => user.value?.username ?? '')

  async function login(username, password) {
    loading.value = true
    error.value   = null
    try {
      const { data } = await authApi.login(username, password)
      localStorage.setItem('access_token',  data.access_token)
      localStorage.setItem('refresh_token', data.refresh_token)
      tokenRevision.value += 1
      await loadMe()
      return true
    } catch (e) {
      error.value = e.response?.data?.detail ?? 'Login fehlgeschlagen'
      return false
    } finally {
      loading.value = false
    }
  }

  async function loadMe() {
    try {
      const { data } = await authApi.me()
      user.value = data
    } catch {
      user.value = null
    }
  }

  function logout() {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    tokenRevision.value += 1
    user.value = null
  }

  return { user, loading, error, isLoggedIn, isAdmin, username, login, loadMe, logout }
})
