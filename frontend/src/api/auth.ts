import service from './index'

export interface RefreshTokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export const authApi = {
  login(data: { username: string; password: string }) {
    return service.post('/auth/login', data)
  },
  register(data: { username: string; email: string; password: string; role?: string; agreed_to_terms?: boolean }) {
    return service.post('/auth/register', data)
  },
  getMe() {
    return service.get('/auth/me')
  },
  refreshToken(refreshToken: string) {
    return service.post<RefreshTokenResponse>('/auth/refresh', { refresh_token: refreshToken })
  },
}
