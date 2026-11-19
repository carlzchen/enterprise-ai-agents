import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router'

const http = axios.create({ baseURL: '/api', timeout: 30000 })

/** 每个请求生成追踪号（后端写入日志并在响应头返回），报错时展示，便于按追踪号查日志 */
export const newTraceId = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/[^A-Za-z0-9]/g, '').slice(0, 16)

http.interceptors.request.use(cfg => {
  const token = localStorage.getItem('token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  cfg.headers['X-Trace-Id'] = newTraceId()
  return cfg
})

http.interceptors.response.use(
  res => res.data,
  err => {
    const status = err.response?.status
    if (status === 401) {
      localStorage.removeItem('token')
      router.push('/login')
    } else {
      const traceId = err.response?.data?.traceId || err.response?.headers?.['x-trace-id']
      ElMessage.error({ message: (err.response?.data?.message || err.message) + (traceId ? `（追踪号 ${traceId}）` : ''), duration: 6000 })
    }
    return Promise.reject(err)
  }
)

export default http
