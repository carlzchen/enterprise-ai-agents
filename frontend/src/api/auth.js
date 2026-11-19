import { reactive } from 'vue'
import http from './http'

export const auth = reactive({
  user: JSON.parse(localStorage.getItem('user') || 'null')
})

export async function login(username, password) {
  const res = await http.post('/auth/login', { username, password })
  localStorage.setItem('token', res.token)
  localStorage.setItem('user', JSON.stringify(res.user))
  auth.user = res.user
}

/** 公司 SSO 登录（只用于企业知识库）：知识库服务完成 OIDC 登录后跳回 /kbase#token=...，这里把凭证收下 */
export function ssoLogin() {
  window.location.href = '/kbase-api/auth/sso/login'
}

export function acceptSsoToken(hash) {
  const token = new URLSearchParams((hash || '').replace(/^#/, '')).get('token')
  if (!token) return false
  const claims = JSON.parse(decodeURIComponent(escape(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))))
  const user = { username: claims.sub, displayName: claims.name, role: claims.role, src: 'sso' }
  localStorage.setItem('token', token)
  localStorage.setItem('user', JSON.stringify(user))
  auth.user = user
  return true
}

export const isSso = () => auth.user?.src === 'sso'

export function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  auth.user = null
}

export function hasRole(roles) {
  if (!roles || roles.length === 0) return true
  return !!auth.user && roles.includes(auth.user.role)
}
