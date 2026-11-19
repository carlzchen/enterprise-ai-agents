export const STATUS = {
  DRAFT: ['草稿', 'info'], CONFIRMED: ['已确认', ''], PARTIAL: ['部分完成', 'warning'], SHIPPED: ['已发货', 'success'],
  CANCELLED: ['已取消', 'info'], INVOICED: ['已开票', 'success'], OPEN: ['未结清', 'warning'], PAID: ['已结清', 'success'],
  RECEIVED: ['已到货', 'success'], PENDING_IQC: ['待检验', 'warning'], COMPLETED: ['已完成', 'success'],
  PLANNED: ['计划', 'info'], RELEASED: ['已下达', ''], IN_PROGRESS: ['生产中', 'warning'],
  PASS: ['合格', 'success'], FAIL: ['判退', 'danger'], ANALYZING: ['分析中', 'warning'], CLOSED: ['已关闭', 'success'],
  SUBMITTED: ['待审批', 'warning'], APPROVED: ['已审批', 'success'], REJECTED: ['已驳回', 'danger'],
  ACTIVE: ['启用', 'success'], CREATED: ['待发出', 'info'], PENDING_APPROVAL: ['价差待审批', 'warning'],
  READY: ['齐套', 'success'], RUNNING: ['审批中', 'warning'], WITHDRAWN: ['已撤回', 'info'], PENDING: ['待审批', 'warning'],
  PROBATION: ['试用', 'warning'], LEFT: ['离职', 'info'], NORMAL: ['正常', 'success'], LATE: ['迟到', 'warning'], ABSENT: ['旷工', 'danger'], LEAVE: ['请假', 'info'], WAIT_PO: ['待到料', 'warning'], SHORTAGE: ['缺料', 'danger'], HIGH: ['高', 'danger'], MEDIUM: ['中', 'warning'], LOW: ['低', 'info']
}

export const SERIES = {
  SHADED_POLE: '罩极电机', CROSSFLOW: '贯流风机', BLDC: '无刷直流', STEPPER: '步进电机', SERVO: '伺服电机', GEAR: '齿轮减速', SEMI: '半成品组件'
}

export const DEFECTS = {
  NOISE: '异响', NO_START: '不启动', OVERHEAT: '过热', SPEED: '转速异常', ENCODER: '编码器故障', APPEARANCE: '外观',
  WINDING: '绕线不良', CURRENT: '电流超标'
}

export const ECN_STATUS = {
  DRAFT: ['草稿', 'info'], REVIEW: ['会签中', 'warning'], APPROVED: ['已批准', ''], RELEASED: ['已发布', 'success'], REJECTED: ['已否决', 'danger']
}
export const REV_STATUS = { RELEASED: ['生效', 'success'], OBSOLETE: ['已作废', 'info'] }
export const DOC_STATUS = { CURRENT: ['现行', 'success'], SUPERSEDED: ['已替代', 'info'] }
export const PROJECT_STATUS = { ACTIVE: ['进行中', ''], ON_HOLD: ['暂停', 'warning'], CLOSED: ['已结项', 'success'], KILLED: ['已终止', 'danger'] }
export const REVIEW_DECISION = { PENDING: ['待会签', 'info'], APPROVE: ['同意', 'success'], REJECT: ['否决', 'danger'] }
export const ECN_REASONS = { COST_DOWN: '降本', QUALITY: '质量改进', CUSTOMER: '客户要求', SUPPLY: '供应变更', DESIGN: '设计优化' }
export const ECN_ACTIONS = { ADD: '新增物料', REMOVE: '删除物料', CHANGE: '修改用量' }
export const STAGES = { CONCEPT: '概念', DESIGN: '设计', PROTOTYPE: '样机', PILOT: '试产', MP: '量产' }
export const DOC_TYPES = { DATASHEET: '规格书', DRAWING: '图纸', SOP: '作业指导书', INSPECTION: '检验标准', TEST_REPORT: '测试报告' }
export const ROLES_CN = { ENGINEER: '研发', PRODUCTION: '生产', QUALITY: '质量', PURCHASE: '采购' }

export const TXN_TYPES = {
  PO_RECEIPT: '采购入库', WO_ISSUE: '生产领料', WO_COMPLETE: '完工入库', SO_DELIVERY: '销售出库', ADJUST: '盘点调整',
  TRANSFER_OUT: '调拨出库', TRANSFER_IN: '调拨入库', COST_ADJ: '价差调整成本'
}

const nf = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const qf = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 })

export function money(v) {
  return v === null || v === undefined || v === '' ? '' : nf.format(Number(v))
}

export function qty(v) {
  return v === null || v === undefined || v === '' ? '' : qf.format(Number(v))
}

export function pct(v, digits = 1) {
  return v === null || v === undefined ? '-' : (Number(v) * 100).toFixed(digits) + '%'
}

/** 金额缩写：万 / 亿 */
export function wan(v) {
  const n = Number(v || 0)
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + ' 亿'
  return (n / 1e4).toFixed(1) + ' 万'
}

export function today() {
  return new Date().toISOString().slice(0, 10)
}
