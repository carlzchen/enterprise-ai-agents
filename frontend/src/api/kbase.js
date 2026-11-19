import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router'
import { newTraceId } from './http'

/** 企业知识库的接口（Python 服务，Nginx 转发到 /kbase-api） */
const BASE = '/kbase-api'
const kbase = axios.create({ baseURL: BASE, timeout: 120000 })
kbase.interceptors.request.use(c => {
  c.headers.Authorization = `Bearer ${localStorage.getItem('token')}`
  c.headers['X-Trace-Id'] = newTraceId()
  return c
})
kbase.interceptors.response.use(r => r.data, err => {
  if (err.response?.status === 401) {
    localStorage.removeItem('token')
    router.push('/login')
  } else if (!err.config?.silent) {
    ElMessage.error(err.response?.data?.detail || (err.code === 'ERR_NETWORK' ? '知识库服务没有启动' : err.message))
  }
  return Promise.reject(err)
})
export default kbase

/** 流式提问：POST 后直接读 SSE。onEvent(事件名, 数据) */
export async function askStream(body, onEvent) {
  const resp = await fetch(`${BASE}/ask/stream`, {
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
    throw new Error(e.detail || (resp.status === 429 ? '当前提问的人比较多，请稍后再试' : `请求失败（${resp.status}）`))
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

export const DOC_TYPES = { DATASHEET: '规格书', MANUAL: '手册', SOP: '作业指导书', INSPECTION: '检验规范', DRAWING: '图纸', TRAINING: '培训资料', PROCESS: '工艺规程', SELECTION: '选型表', POLICY: '规章制度' }
export const LEVELS = { PUBLIC: '公开', INTERNAL: '内部', CONFIDENTIAL: '机密' }
export const ROLES = { ALL: '全员', SALES: '销售', ENGINEER: '研发', QUALITY: '质量', PRODUCTION: '生产', PURCHASE: '采购', WAREHOUSE: '仓库', FINANCE: '财务', HR: '人力', MANAGEMENT: '管理层' }
export const fmtTime = t => (t ? String(t).slice(0, 19).replace('T', ' ') : '')
