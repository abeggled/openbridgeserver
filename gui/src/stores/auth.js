import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const user    = ref(null)   // { id, username, is_admin, created_at }
  const loading = ref(false)
  const error   = ref(null)

  const isLoggedIn  = computed(() => !!localStorage.getItem('access_token'))
  const isAdmin     = computed(() => user.value?.is_admin ?? false)
  const username    = computed(() => user.value?.username ?? '')

  async function login(username, password) {
    loading.value = true
    error.value   = null
    try {
      const { data } = await authApi.login(username, password)
      localStorage.setItem('access_token',  data.access_token)
      localStorage.setItem('refresh_token', data.refresh_token)
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
    user.value = null
  }

  return { user, loading, error, isLoggedIn, isAdmin, username, login, loadMe, logout }
})
