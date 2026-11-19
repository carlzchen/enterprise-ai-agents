import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router'
import { newTraceId } from './http'

/** 销售与采购跟单 Workflow 的接口（Python 服务，Nginx 转发到 /workflow-api） */
const workflow = axios.create({ baseURL: '/workflow-api', timeout: 120000 })
workflow.interceptors.request.use(c => {
  c.headers.Authorization = `Bearer ${localStorage.getItem('token')}`
  c.headers['X-Trace-Id'] = newTraceId()
  return c
})
workflow.interceptors.response.use(r => r.data, err => {
  if (err.response?.status === 401) {
    localStorage.removeItem('token')
    router.push('/login')
  } else if (!err.config?.silent) {
    ElMessage.error(err.response?.data?.detail || (err.code === 'ERR_NETWORK' ? '跟单服务没有启动' : err.message))
  }
  return Promise.reject(err)
})
export default workflow

export const ROLE_CN = { SALES: '销售', ENGINEER: '技术', PRODUCTION: '计划', FINANCE: '财务', PURCHASE: '采购', QUALITY: '质量', MANAGEMENT: '管理层', ADMIN: '管理员' }
export const TASK_TAG = { NONSTD_REVIEW: 'danger', TRIAGE: 'warning', CLARIFY: '', SEND: '', SELECTION: 'success', QUOTE_REVIEW: 'success', APPROVAL: 'danger', NEGOTIATION: 'warning', PO_ISSUE: 'danger', ERP_IMPORT: 'info', ASSIGN: 'info' }
export const OB_CN = { CLARIFY: '澄清邮件', QUOTE: '报价邮件', COUNTER_REPLY: '议价回复', ORDER_CONFIRM: '订单确认书', DELIVERY_REPLY: '进度回复' }
export const ADOPT_CN = { AS_IS: '直接采用', EDITED: '修改后采用', DISCARDED: '弃用' }
export const fmt = t => (t ? String(t).slice(0, 16).replace('T', ' ') : '')
export const money = v => (v == null ? '-' : Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
export const pct = v => (v == null ? '-' : (Number(v) * 100).toFixed(1) + '%')
