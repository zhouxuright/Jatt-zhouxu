import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/api/auth'
import router from '@/router'

export interface UserInfo {
  id: string
  username: string
  email: string
  avatar?: string
  created_at: string
  /** "user" | "admin" — returned by /auth/me and /auth/login. */
  role?: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>('')
  const user = ref<UserInfo | null>(null)
  const isAuthenticated = computed(() => !!token.value && !!user.value)
  const isAdmin = computed(() => user.value?.role === 'admin')

  function setToken(newToken: string, refreshToken?: string) {
    token.value = newToken
    localStorage.setItem('token', newToken)
    if (refreshToken) {
      localStorage.setItem('refresh_token', refreshToken)
    }
  }

  function clearToken() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('refresh_token')
  }

  /**
   * Restore auth state from localStorage on app mount.
   * Only restores the token synchronously; fetchUser is called
   * separately after navigation to avoid race conditions.
   */
  function restoreAuth() {
    const savedToken = localStorage.getItem('token')
    if (savedToken && !token.value) {
      token.value = savedToken
      // Don't call fetchUser here — it will be called after
      // navigation completes to avoid 401 race conditions
    }
  }

  async function fetchUser() {
    // Only fetch if we have a token but no user data
    if (!token.value) return
    if (user.value) return  // Already fetched

    try {
      const res = await authApi.getMe()
      user.value = res.data
    } catch {
      // Don't clear token on fetch failure — it might be a transient error
      // Token will be cleared on next actual API call if truly invalid
      console.warn('Failed to fetch user profile, will retry on next request')
    }
  }

  async function login(username: string, password: string) {
    const res = await authApi.login({ username, password })
    const data = res.data
    // Backend returns {token: {access_token, refresh_token, ...}, user: {...}}
    const accessToken = data.token?.access_token || data.access_token
    const refreshToken = data.token?.refresh_token || data.refresh_token
    setToken(accessToken, refreshToken)
    user.value = data.user
    return data
  }

  async function register(data: { username: string; email: string; password: string; agreed_to_terms?: boolean }) {
    const res = await authApi.register(data)
    return res.data
  }

  function logout() {
    clearToken()
    router.push('/login')
  }

  return {
    token,
    user,
    isAuthenticated,
    isAdmin,
    setToken,
    clearToken,
    restoreAuth,
    fetchUser,
    login,
    register,
    logout,
  }
})
