import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router'
import { newTraceId } from './http'

/** 经营与人力数据分析 Copilot 的接口（Python 服务，Nginx 转发到 /copilot-api） */
const BASE = '/copilot-api'
const copilot = axios.create({ baseURL: BASE, timeout: 120000 })
copilot.interceptors.request.use(c => {
  c.headers.Authorization = `Bearer ${localStorage.getItem('token')}`
  c.headers['X-Trace-Id'] = newTraceId()
  return c
})
copilot.interceptors.response.use(r => r.data, err => {
  if (err.response?.status === 401) {
    localStorage.removeItem('token')
    router.push('/login')
  } else if (!err.config?.silent) {
    ElMessage.error(err.response?.data?.detail || (err.code === 'ERR_NETWORK' ? 'Copilot 服务没有启动' : err.message))
  }
  return Promise.reject(err)
})
export default copilot

/** 流式提问：POST 后直接读 SSE。onEvent(事件名, 数据) */
export async function chatStream(body, onEvent) {
  const resp = await fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('token')}`, 'X-Trace-Id': newTraceId() },
    body: JSON.stringify(body)
  })
  if (resp.status === 401) {
    localStorage.removeItem('token')
    router.push('/login')
    throw new Error('登录已失效，请重新登录')
  }
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({}))
    throw new Error(e.detail || `请求失败（${resp.status}）`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i)
      buf = buf.slice(i + 2)
      const event = /^event: (.*)$/m.exec(block)?.[1]
      const data = /^data: (.*)$/m.exec(block)?.[1]
      if (event && data) onEvent(event, JSON.parse(data))
    }
  }
}

/** 按列类型格式化单元格：比率显示百分数，金额带千分位 */
export function fmtCell(v, type) {
  if (v === null || v === undefined || v === '') return '—'
  if (type === 'pct') return (v * 100).toFixed(2) + '%'
  if (type === 'money') return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (type === 'int') return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
  if (type === 'num') return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  return v
}
