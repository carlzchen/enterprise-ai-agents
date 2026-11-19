import { markRaw } from 'vue'
import ListPage from './views/ListPage.vue'
import SalesOrderDetail from './views/SalesOrderDetail.vue'
import PurchaseOrderDetail from './views/PurchaseOrderDetail.vue'
import GoodsReceiptDetail from './views/GoodsReceiptDetail.vue'
import EcnDetail from './views/EcnDetail.vue'
import BomRevisionDetail from './views/BomRevisionDetail.vue'
import ProjectDetail from './views/ProjectDetail.vue'
import RfqDetail from './views/RfqDetail.vue'
import VoucherDetail from './views/VoucherDetail.vue'
import TransferDetail from './views/TransferDetail.vue'
import PayrollDetail from './views/PayrollDetail.vue'
import WfDetail from './views/WfDetail.vue'
import EmployeeDetail from './views/EmployeeDetail.vue'
import EquipmentDetail from './views/EquipmentDetail.vue'
import EightDDetail from './views/EightDDetail.vue'
import SerialDetail from './views/SerialDetail.vue'
import FaAssetDetail from './views/FaAssetDetail.vue'
import http from './api/http'
import { openForm, options, opt, enumOpts } from './api/shared'
import { money, SERIES, DEFECTS, TXN_TYPES, STATUS, today, ECN_STATUS, ECN_REASONS, ECN_ACTIONS, REV_STATUS, DOC_STATUS, DOC_TYPES, PROJECT_STATUS, STAGES } from './api/format'

const FIN = ['ADMIN', 'FINANCE', 'MANAGEMENT']
const SUPPLY = { BUY: ['外购', 'info'], MAKE: ['自制', 'warning'] }
const WH_TYPES = { RAW: ['原材料仓', 'info'], SEMI: ['半成品仓', 'warning'], FG: ['成品总仓', ''], DC: ['前置仓', 'success'] }
const TR_STATUS = { CREATED: ['待发出', 'info'], SHIPPED: ['在途', 'warning'], RECEIVED: ['已到货', 'success'], CANCELLED: ['已取消', 'info'] }
const CHANGE_TYPES = { HIRE: ['入职', 'success'], REGULAR: ['转正', ''], TRANSFER: ['调岗', 'warning'], SALARY: ['调薪', 'warning'], LEAVE: ['离职', 'info'] }
const LEAVE_TYPES = { ANNUAL: ['年假', 'success'], SICK: ['病假', 'warning'], PERSONAL: ['事假', 'info'] }
const PAYROLL_STATUS = { DRAFT: ['草稿', 'info'], PENDING: ['审批中', 'warning'], APPROVED: ['已审批入账', ''], PAID: ['已发放', 'success'] }
const EAM_TYPES = { BREAKDOWN: ['故障维修', 'danger'], PM: ['预防保养', ''] }
const EAM_STATUS = { OPEN: ['待维修', 'danger'], IN_PROGRESS: ['维修中', 'warning'], DONE: ['已完成', 'success'] }
const EQ_STATUS = { RUNNING: ['运行', 'success'], DOWN: ['故障', 'danger'], MAINT: ['保养中', 'warning'], SCRAPPED: ['报废', 'info'] }
const ANDON_TYPES = { MATERIAL: ['缺料', 'warning'], EQUIPMENT: ['设备', 'danger'], QUALITY: ['质量', ''] }
const GAUGE_STATUS = { OK: ['正常', 'success'], LOCKED: ['停用', 'danger'], SCRAPPED: ['报废', 'info'] }
const LINES = ['L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8'].map(v => ({ value: v, label: v }))
const WO_TYPES = { PRODUCT: ['成品', ''], MATERIAL: ['组件', 'warning'] }
const WAREHOUSES = [['WH-RAW', '原材料仓'], ['WH-SEMI', '半成品仓'], ['WH-FG', '成品总仓'], ['WH-FG-EC', '华东前置仓'], ['WH-FG-HK', '香港出口仓']]
  .map(([value, label]) => ({ value, label }))
const st = (...codes) => codes.map(c => ({ value: c, label: STATUS[c][0] }))
const list = config => ({ component: ListPage, config })
const plusDays = n => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10)

// ---------------- 表单 ----------------

async function newTransfer(ctx) {
  const o = await options(true)
  const items = [...o.products.map(p => ({ value: `PRODUCT:${p.id}`, label: `${p.code} ${p.name}` })),
    ...o.materials.map(m => ({ value: `MATERIAL:${m.id}`, label: `${m.code} ${m.name}` }))]
  const whs = o.warehouses.map(w => ({ value: w.id, label: w.name }))
  openForm({
    title: '新建调拨单',
    width: '760px',
    fields: [
      { prop: 'fromWarehouseId', label: '调出仓', type: 'select', required: true, options: whs },
      { prop: 'toWarehouseId', label: '调入仓', type: 'select', required: true, options: whs },
      { prop: 'remark', label: '备注' },
      { prop: 'lines', label: '调拨明细', type: 'lines', required: true, newLine: { qty: 100 },
        columns: [{ prop: 'item', label: '物料 / 产品', type: 'select', width: 320, options: items }, { prop: 'qty', label: '数量', precision: 3 }] }
    ],
    initial: { fromWarehouseId: o.warehouses.find(w => w.type === 'FG')?.id, toWarehouseId: o.warehouses.find(w => w.type === 'DC')?.id, lines: [{ qty: 100 }] },
    onSubmit: async m => {
      const lines = m.lines.filter(l => l.item).map(l => { const [itemType, itemId] = l.item.split(':'); return { itemType, itemId: Number(itemId), qty: l.qty } })
      await http.post('/transfers', { ...m, date: today(), lines })
      ctx.ElMessage.success('调拨单已创建，请在详情中发出')
      ctx.reload()
    }
  })
}

async function transfersFromSuggestions(ctx) {
  const s = await http.get('/transfers/suggestions')
  if (!s.length) return ctx.ElMessage.info('各前置仓可用量都在订货点以上，无需补货')
  await ctx.ElMessageBox.confirm(s.map(x => `${x.warehouse_name}｜${x.code}：日均 ${x.daily}，在库 ${Number(x.on_hand)}，在途 ${Number(x.in_transit)} → 补 ${Number(x.suggest_qty)}`).join('<br>'),
    `补货建议（${s.length} 项，订货点 7 天 / 目标 14 天）`, { dangerouslyUseHTMLString: true, confirmButtonText: '生成调拨单' })
  const ids = await http.post('/transfers/from-suggestions', { date: today() })
  ctx.ElMessage.success(`已生成 ${ids.length} 张调拨单`)
  ctx.reload()
}

function newPieceRate(ctx) {
  openForm({
    title: '调整计件单价',
    fields: [{ prop: 'lineCode', label: '产线', type: 'select', required: true, options: ['L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8'].map(v => ({ value: v, label: v })) },
      { prop: 'rate', label: '单价（元/台良品）', type: 'number', precision: 4, required: true },
      { prop: 'effectiveFrom', label: '生效日期', type: 'date', required: true }, { prop: 'remark', label: '说明' }],
    initial: { effectiveFrom: today() },
    onSubmit: async m => { await http.post('/hr/piece-rates', m); ctx.ElMessage.success('已保存，按报工日期取有效单价'); ctx.reload() }
  })
}

async function calcPayroll(ctx) {
  const { value } = await ctx.ElMessageBox.prompt('核算期间（YYYY-MM）', '核算工资', { inputValue: new Date().toISOString().slice(0, 7), inputPattern: /^\d{4}-\d{2}$/ })
  await http.post('/hr/payrolls/calculate', { period: value })
  ctx.ElMessage.success(`${value} 工资已核算（草稿），请打开核对后提交审批`)
  ctx.reload()
}

async function reportBreakdown(ctx) {
  const eq = (await http.get('/views/equipment', { params: { size: 100, sort: 'code', order: 'asc' } })).records
  openForm({
    title: '设备报修（同时拉设备安灯）',
    fields: [{ prop: 'equipmentId', label: '设备', type: 'select', required: true, options: eq.map(e => ({ value: e.id, label: `${e.code} ${e.name}` })) },
      { prop: 'faultCode', label: '故障代码' }, { prop: 'desc', label: '故障现象', required: true }],
    onSubmit: async m => { await http.post('/eam/breakdowns', m); ctx.ElMessage.success('已报修，设备状态改为故障'); ctx.reload() }
  })
}

function completeRepair(r, ctx) {
  openForm({
    title: `维修完工 - ${r.wo_no}`,
    fields: [{ prop: 'cause', label: '故障原因', required: true }, { prop: 'action', label: '处理措施', required: true },
      { prop: 'laborHours', label: '维修工时', type: 'number', precision: 2 }],
    onSubmit: async m => { await http.post(`/eam/work-orders/${r.id}/complete`, { ...m, spares: [] }); ctx.ElMessage.success('已完工，设备恢复运行'); ctx.reload() }
  })
  return false
}

async function aqlPlan(ctx) {
  const { value } = await ctx.ElMessageBox.prompt('批量,AQL（如 1000,0.65）', 'GB/T 2828.1 抽样方案（一般检验水平 II，正常一次抽样）', { inputValue: '1000,0.65' })
  const [lotSize, aql] = value.split(',').map(Number)
  const p = await http.get('/qms/aql-plan', { params: { lotSize, aql } })
  ctx.ElMessageBox.alert(`样本量字码 ${p.codeLetter}，抽样 ${p.sampleSize}，Ac=${p.ac}，Re=${p.re}`, `批量 ${lotSize}，AQL ${aql}`)
}

async function newCapex(ctx) {
  openForm({
    title: '资本性支出申请（审批通过后自动入账建卡）',
    fields: [{ prop: 'name', label: '资产名称', required: true },
      { prop: 'categoryCode', label: '类别', type: 'select', required: true, options: [['MACHINE', '机器设备'], ['MOLD', '模具工装'], ['VEHICLE', '运输设备'], ['IT', '电子及办公设备'], ['BUILDING', '房屋及建筑物']].map(([value, label]) => ({ value, label })) },
      { prop: 'deptCode', label: '使用部门', type: 'select', required: true, options: [['WS1', '家电电机车间'], ['WS2', '智能驱控车间'], ['RND', '研发中心'], ['QA', '质量部'], ['IT', '信息部'], ['ADM', '行政部'], ['LOG', '仓储物流部']].map(([value, label]) => ({ value, label })) },
      { prop: 'cost', label: '原值（不含税）', type: 'number', precision: 2, required: true }, { prop: 'inServiceDate', label: '预计投用', type: 'date', required: true },
      { prop: 'reason', label: '申请理由', required: true }],
    initial: { categoryCode: 'MACHINE', inServiceDate: today() },
    onSubmit: async m => { await http.post('/fa/capex', m); ctx.ElMessage.success('已提交审批：部门负责人 → 设备动力部 → 财务负责人 → 总经理（≥20 万）') }
  })
}

async function newContract(ctx) {
  const o = await options()
  openForm({
    title: '起草合同',
    fields: [{ prop: 'type', label: '类型', type: 'select', required: true, options: [['SALES', '销售框架'], ['PURCHASE', '采购框架'], ['SERVICE', '服务'], ['NDA', '保密协议']].map(([value, label]) => ({ value, label })) },
      { prop: 'counterpartyType', label: '相对方类型', type: 'select', options: [['CUSTOMER', '客户'], ['SUPPLIER', '供应商'], ['OTHER', '其他']].map(([value, label]) => ({ value, label })) },
      { prop: 'counterpartyId', label: '客户 / 供应商', type: 'select', options: [...opt(o.customers), ...opt(o.suppliers)] },
      { prop: 'counterpartyName', label: '相对方名称（其他）' }, { prop: 'title', label: '合同标题', required: true },
      { prop: 'amount', label: '金额', type: 'number', precision: 2 }, { prop: 'paymentTerms', label: '付款条件' },
      { prop: 'startDate', label: '开始', type: 'date', required: true }, { prop: 'endDate', label: '结束', type: 'date', required: true }],
    initial: { type: 'SALES', counterpartyType: 'CUSTOMER', startDate: today(), endDate: plusDays(365) },
    onSubmit: async m => { const r = await http.post('/contracts', m); await http.post(`/contracts/${r.id}/submit`); ctx.ElMessage.success(`${r.contract_no} 已提交审批`); ctx.reload() }
  })
}

const IMPACT = [['HIGH', '高（全厂 / 产线）'], ['MEDIUM', '中（一个部门）'], ['LOW', '低（个人）']].map(([value, label]) => ({ value, label }))
const URGENCY = [['HIGH', '高（业务已中断）'], ['MEDIUM', '中'], ['LOW', '低']].map(([value, label]) => ({ value, label }))
const DEPT_CODES = [['WS1', '家电电机车间'], ['WS2', '智能驱控车间'], ['LOG', '仓储物流部'], ['EQP', '设备动力部'], ['QA', '质量部'], ['RND', '研发中心'], ['ADM', '行政部']]
  .map(([value, label]) => ({ value, label }))

function newItTicket(ctx) {
  openForm({
    title: '提交 IT 工单',
    fields: [{ prop: 'type', label: '类型', type: 'select', required: true, options: [{ value: 'INCIDENT', label: '故障' }, { value: 'REQUEST', label: '服务请求' }] },
      { prop: 'category', label: '分类', type: 'select', required: true, options: ['电脑故障', '网络', '打印机', '账号密码', '邮箱', 'ERP', 'MES', '权限申请', '软件安装', '设备申领'].map(v => ({ value: v, label: v })) },
      { prop: 'title', label: '标题', required: true }, { prop: 'description', label: '描述', type: 'textarea' },
      { prop: 'impact', label: '影响范围', type: 'select', required: true, options: IMPACT }, { prop: 'urgency', label: '紧急程度', type: 'select', required: true, options: URGENCY }],
    initial: { type: 'INCIDENT', impact: 'LOW', urgency: 'MEDIUM' },
    onSubmit: async m => { await http.post('/itsm/tickets', m); ctx.ElMessage.success('已提交，信息部将按优先级响应'); ctx.reload() }
  })
}

function newServiceTicket(ctx) {
  openForm({
    title: '新建售后服务工单',
    fields: [{ prop: 'sn', label: '序列号（伺服）', required: true }, { prop: 'type', label: '类型', type: 'select', required: true,
      options: [{ value: 'FAULT', label: '故障' }, { value: 'CONSULT', label: '技术咨询' }, { value: 'INSTALL', label: '安装调试' }] },
      { prop: 'priority', label: '优先级', type: 'select', required: true, options: [{ value: 'P1', label: 'P1 客户停线' }, { value: 'P2', label: 'P2' }, { value: 'P3', label: 'P3' }] },
      { prop: 'faultCode', label: '故障', type: 'select', options: Object.entries(FAULTS).map(([value, label]) => ({ value, label })) },
      { prop: 'symptom', label: '现象', type: 'textarea' }],
    initial: { type: 'FAULT', priority: 'P2', channel: 'PHONE' },
    onSubmit: async m => { await http.post('/aftersales/tickets', { ...m, channel: 'PHONE' }); ctx.ElMessage.success('工单已创建（保修状态按序列号自动判定）'); ctx.reload() }
  })
}

function reportHazard(ctx) {
  openForm({
    title: '隐患随手拍',
    fields: [{ prop: 'deptCode', label: '所在部门', type: 'select', required: true, options: DEPT_CODES }, { prop: 'location', label: '位置', required: true },
      { prop: 'category', label: '类别', type: 'select', required: true, options: ['电气', '机械', '消防', '化学品', '叉车', '作业环境', '职业健康', '能源'].map(v => ({ value: v, label: v })) },
      { prop: 'level', label: '等级', type: 'select', options: [{ value: 'GENERAL', label: '一般（7 天整改）' }, { value: 'MAJOR', label: '重大（30 天整改）' }] },
      { prop: 'description', label: '描述', type: 'textarea', required: true }],
    initial: { level: 'GENERAL' },
    onSubmit: async m => { await http.post('/ehs/hazards', m); ctx.ElMessage.success('已上报，整改责任人为部门负责人'); ctx.reload() }
  })
}

const FAULTS = { NOISE: '异响振动', ENCODER: '编码器故障', OVERHEAT: '过热', NO_START: '不启动', SPEED: '速度异常', PARAM: '参数设置' }
const PRIO = { P1: ['P1', 'danger'], P2: ['P2', 'warning'], P3: ['P3', ''], P4: ['P4', 'info'] }
const opts = pairs => pairs.map(([value, label]) => ({ value, label }))

async function vatReturn(ctx) {
  const { value } = await ctx.ElMessageBox.prompt('月结期间（YYYY-MM）', '增值税月结（免抵退）', { inputValue: new Date().toISOString().slice(0, 7), inputPattern: /^\d{4}-\d{2}$/ })
  const r = await http.post(`/customs/vat/${value}`)
  ctx.ElMessageBox.alert(`应纳 ${money(r.payable)}，应退 ${money(r.refund)}，免抵 ${money(r.exempt_offset)}，期末留抵 ${money(r.end_credit)}，附加税 ${money(r.surtax)}`, `${value} 增值税月结`)
  ctx.reload()
}

async function newSubstitute(ctx) {
  const o = await options()
  const mats = opt(o.materials.filter(m => m.supplyType !== 'MAKE'), x => `${x.code} ${x.name}（${x.uom}）`)
  openForm({
    title: '新增替代料',
    fields: [
      { prop: 'materialId', label: '主料', type: 'select', required: true, options: mats },
      { prop: 'substituteId', label: '替代料', type: 'select', required: true, options: mats },
      { prop: 'ratio', label: '替代比例（1 主料 = ? 替代料）', type: 'number', precision: 4 },
      { prop: 'priority', label: '优先级', type: 'number', precision: 0 },
      { prop: 'validFrom', label: '生效日期', type: 'date' },
      { prop: 'reason', label: '替代依据' }
    ],
    initial: { ratio: 1, priority: 1, validFrom: today() },
    onSubmit: async m => {
      await http.post('/md/substitutes', m)
      ctx.ElMessage.success('替代料已保存，排产齐套与生产领料将自动使用')
      ctx.reload()
    }
  })
}

async function newSalesOrder(ctx) {
  const o = await options()
  const products = opt(o.products)
  openForm({
    title: '新建销售订单',
    width: '860px',
    fields: [
      { prop: 'customerId', label: '客户', type: 'select', required: true, options: opt(o.customers) },
      { prop: 'orderDate', label: '下单日期', type: 'date', required: true },
      { prop: 'requiredDate', label: '要求交期', type: 'date', required: true },
      { prop: 'salesperson', label: '销售员' },
      {
        prop: 'lines', label: '订单行（单价为客户结算币种、不含税）', type: 'lines', required: true, newLine: { qty: 1000 },
        columns: [
          { prop: 'productId', label: '产品', type: 'select', width: 260, options: products,
            onChange: (row, m) => {
              const p = o.products.find(x => x.id === row.productId)
              const c = o.customers.find(x => x.id === m.customerId)
              if (p && (!c || c.currency === 'CNY')) row.unitPrice = Number(p.listPrice)
            } },
          { prop: 'qty', label: '数量', precision: 0 },
          { prop: 'unitPrice', label: '单价', precision: 4 }
        ]
      }
    ],
    initial: { orderDate: today(), requiredDate: plusDays(21), lines: [{ qty: 1000 }] },
    onSubmit: async m => {
      await http.post('/sales/orders', m)
      ctx.ElMessage.success('订单已创建（草稿），请在详情中确认')
      ctx.reload()
    }
  })
}

async function newPurchaseOrder(ctx) {
  const o = await options()
  openForm({
    title: '新建采购订单',
    width: '820px',
    fields: [
      { prop: 'supplierId', label: '供应商', type: 'select', required: true, options: opt(o.suppliers) },
      { prop: 'orderDate', label: '下单日期', type: 'date', required: true },
      { prop: 'expectedDate', label: '预计到货', type: 'date', required: true },
      { prop: 'buyer', label: '采购员' },
      {
        prop: 'lines', label: '采购行（单价留空按当月参考价）', type: 'lines', required: true,
        columns: [
          { prop: 'materialId', label: '物料', type: 'select', width: 260, options: opt(o.materials, x => `${x.code} ${x.name}（${x.uom}）`),
            onChange: row => { row.unitPrice = Number(o.materials.find(x => x.id === row.materialId)?.latestPrice || 0) } },
          { prop: 'qty', label: '数量', precision: 3 },
          { prop: 'unitPrice', label: '单价', precision: 4 }
        ]
      }
    ],
    initial: { orderDate: today(), expectedDate: plusDays(7), lines: [{}] },
    onSubmit: async m => {
      await http.post('/purchase/orders', m)
      ctx.ElMessage.success('采购订单已创建（草稿）')
      ctx.reload()
    }
  })
}

async function newWorkOrder(ctx) {
  const o = await options()
  openForm({
    title: '新建生产工单',
    fields: [
      { prop: 'productId', label: '产品', type: 'select', required: true, options: opt(o.products) },
      { prop: 'qty', label: '计划数量', type: 'number', precision: 0, required: true },
      { prop: 'planStart', label: '计划开工', type: 'date', required: true },
      { prop: 'planEnd', label: '计划完工', type: 'date', required: true }
    ],
    initial: { qty: 1000, planStart: today(), planEnd: plusDays(5) },
    onSubmit: async m => {
      await http.post('/production/work-orders', m)
      ctx.ElMessage.success('工单已创建')
      ctx.reload()
    }
  })
}

function reportWork(row) {
  return new Promise((resolve, reject) => openForm({
    title: `报工 - ${row.wo_no}`,
    fields: [
      { prop: 'goodQty', label: '良品数', type: 'number', precision: 0, required: true },
      { prop: 'scrapQty', label: '报废数', type: 'number', precision: 0 },
      { prop: 'defectCode', label: '不良代码', type: 'select', options: enumOpts(DEFECTS) },
      { prop: 'operator', label: '班组' }
    ],
    initial: { goodQty: Number(row.planned_qty) - Number(row.good_qty) - Number(row.scrap_qty), scrapQty: 0, operator: `${row.line_code}-班组A` },
    onSubmit: async m => {
      await http.post(`/production/work-orders/${row.id}/report`, { ...m, date: today() }).catch(e => { reject(e); throw e })
      resolve()
    }
  }))
}

async function newComplaint(ctx) {
  const o = await options()
  openForm({
    title: '登记客户投诉',
    fields: [
      { prop: 'customerId', label: '客户', type: 'select', required: true, options: opt(o.customers) },
      { prop: 'productId', label: '产品', type: 'select', required: true, options: opt(o.products) },
      { prop: 'defectType', label: '不良类型', type: 'select', required: true, options: enumOpts(DEFECTS) },
      { prop: 'qtyAffected', label: '涉及数量', type: 'number', precision: 0, required: true },
      { prop: 'severity', label: '严重度', type: 'select', required: true, options: st('LOW', 'MEDIUM', 'HIGH') },
      { prop: 'owner', label: '负责人' }
    ],
    initial: { severity: 'MEDIUM', receivedDate: today() },
    onSubmit: async m => {
      await http.post('/quality/complaints', m)
      ctx.ElMessage.success('客诉已登记')
      ctx.reload()
    }
  })
}

function closeComplaint(row) {
  return new Promise((resolve, reject) => openForm({
    title: `8D 关闭 - ${row.complaint_no}`,
    width: '640px',
    fields: [
      { prop: 'rootCause', label: '根本原因', type: 'textarea', required: true },
      { prop: 'correctiveAction', label: '纠正措施', type: 'textarea', required: true }
    ],
    initial: { rootCause: row.root_cause, correctiveAction: row.corrective_action },
    onSubmit: async m => {
      await http.put(`/quality/complaints/${row.id}`, { ...m, status: 'CLOSED' }).catch(e => { reject(e); throw e })
      resolve()
    }
  }))
}

function newExpense(ctx) {
  const cats = ['差旅费', '业务招待费', '办公费', '运输费', '展会费', '研发材料费', '水电费', '维修费']
  const depts = ['销售部', '采购部', '生产部', '质量部', '研发中心', '财务部', '行政部']
  openForm({
    title: '提交费用报销',
    fields: [
      { prop: 'dept', label: '部门', type: 'select', required: true, options: depts.map(v => ({ value: v, label: v })) },
      { prop: 'category', label: '费用类别', type: 'select', required: true, options: cats.map(v => ({ value: v, label: v })) },
      { prop: 'amount', label: '金额', type: 'number', required: true },
      { prop: 'expenseDate', label: '发生日期', type: 'date', required: true },
      { prop: 'description', label: '事由', type: 'textarea' }
    ],
    initial: { expenseDate: today() },
    onSubmit: async m => {
      await http.post('/finance/expenses', m)
      ctx.ElMessage.success('已提交，等待财务审批')
      ctx.reload()
    }
  })
}

function amountPrompt(title, max, submit) {
  return new Promise((resolve, reject) => openForm({
    title,
    fields: [
      { prop: 'amount', label: '金额', type: 'number', required: true },
      { prop: 'date', label: '日期', type: 'date', required: true }
    ],
    initial: { amount: Number(max), date: today() },
    onSubmit: async m => {
      await submit(m).catch(e => { reject(e); throw e })
      resolve()
    }
  }))
}

// ---------------- PLM 表单 ----------------

async function newEcn(ctx) {
  const o = await options()
  openForm({
    title: '新建工程变更（ECN）',
    width: '900px',
    fields: [
      { prop: 'title', label: '变更标题', required: true },
      { prop: 'reason', label: '变更原因', type: 'select', required: true, options: enumOpts(ECN_REASONS) },
      { prop: 'description', label: '变更说明', type: 'textarea' },
      {
        prop: 'lines', label: '变更明细（同一 ECN 可涉及多个产品）', type: 'lines', required: true, newLine: { action: 'CHANGE' },
        columns: [
          { prop: 'productId', label: '产品', type: 'select', width: 220, options: opt(o.products) },
          { prop: 'action', label: '动作', type: 'select', width: 110, options: enumOpts(ECN_ACTIONS) },
          { prop: 'materialId', label: '物料', type: 'select', width: 220, options: opt(o.materials, x => `${x.code} ${x.name}`) },
          { prop: 'newQty', label: '新用量', precision: 4 }
        ]
      }
    ],
    initial: { reason: 'COST_DOWN', lines: [{ action: 'CHANGE' }] },
    onSubmit: async m => {
      await http.post('/plm/ecns', m)
      ctx.ElMessage.success('ECN 已创建（草稿），请在详情中查看影响分析并提交会签')
      ctx.reload()
    }
  })
}

async function uploadDoc(ctx, row) {
  const o = await options()
  const fields = row
    ? [{ prop: 'title', label: '标题' }, { prop: 'file', label: '新版本文件', type: 'file', required: true }]
    : [
        { prop: 'title', label: '文档标题', required: true },
        { prop: 'docType', label: '文档类型', type: 'select', required: true, options: enumOpts(DOC_TYPES) },
        { prop: 'productId', label: '关联产品', type: 'select', options: opt(o.products) },
        { prop: 'series', label: '关联系列', type: 'select', options: enumOpts(SERIES) },
        { prop: 'file', label: '文件', type: 'file', required: true }
      ]
  return new Promise((resolve, reject) => openForm({
    title: row ? `上传新版本 - ${row.doc_no}` : '上传技术文档',
    fields,
    initial: row ? { title: row.title } : { docType: 'DATASHEET' },
    onSubmit: async m => {
      const fd = new FormData()
      fd.append('file', m.file)
      if (row) fd.append('docNo', row.doc_no)
      for (const k of ['title', 'docType', 'productId', 'series']) if (m[k] != null && m[k] !== '') fd.append(k, m[k])
      await http.post('/plm/documents', fd).catch(e => { reject(e); throw e })
      if (!row) {
        ctx.ElMessage.success('文档已上传')
        ctx.reload()
      }
      resolve()
    }
  }))
}

async function downloadDoc(row) {
  const blob = await http.get(`/plm/documents/${row.id}/download`, { responseType: 'blob' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = row.file_name
  a.click()
  URL.revokeObjectURL(url)
  return false
}

function newProject(ctx) {
  openForm({
    title: '新产品开发立项',
    fields: [
      { prop: 'name', label: '项目名称', required: true },
      { prop: 'series', label: '产品系列', type: 'select', required: true, options: enumOpts(SERIES) },
      { prop: 'owner', label: '项目经理', required: true },
      { prop: 'targetCustomer', label: '目标客户/市场' },
      { prop: 'startDate', label: '立项日期', type: 'date', required: true },
      { prop: 'targetMpDate', label: '计划量产', type: 'date', required: true },
      { prop: 'budget', label: '研发预算', type: 'number', required: true }
    ],
    initial: { startDate: today() },
    onSubmit: async m => {
      await http.post('/plm/projects', m)
      ctx.ElMessage.success('项目已立项（概念阶段）')
      ctx.reload()
    }
  })
}

// ---------------- SRM / CRM / GL 表单 ----------------

async function newRfq(ctx) {
  const o = await options()
  openForm({
    title: '新建询价单',
    fields: [
      { prop: 'materialId', label: '物料', type: 'select', required: true, options: opt(o.materials, x => `${x.code} ${x.name}`) },
      { prop: 'qty', label: '数量', type: 'number', precision: 3, required: true },
      { prop: 'needDate', label: '需求日期', type: 'date', required: true }
    ],
    initial: { needDate: plusDays(14) },
    onSubmit: async m => { await http.post('/srm/rfqs', m); ctx.ElMessage.success('询价单已创建，请录入各供应商报价'); ctx.reload() }
  })
}

async function newOpportunity(ctx) {
  const o = await options()
  openForm({
    title: '新建商机',
    fields: [
      { prop: 'customerId', label: '客户', type: 'select', required: true, options: opt(o.customers) },
      { prop: 'title', label: '商机名称', required: true },
      { prop: 'series', label: '意向系列', type: 'select', required: true, options: enumOpts(SERIES) },
      { prop: 'amount', label: '预计年采购额', type: 'number', required: true },
      { prop: 'expectedClose', label: '预计成交', type: 'date', required: true }
    ],
    initial: { expectedClose: plusDays(90) },
    onSubmit: async m => { await http.post('/crm/opportunities', m); ctx.ElMessage.success('商机已创建'); ctx.reload() }
  })
}

const NEXT_STAGE = { LEAD: 'QUALIFIED', QUALIFIED: 'PROPOSAL', PROPOSAL: 'NEGOTIATION', NEGOTIATION: 'WON' }

function newActivity(row) {
  return new Promise((resolve, reject) => openForm({
    title: `跟进 - ${row.title}`,
    fields: [
      { prop: 'type', label: '方式', type: 'select', required: true, options: Object.entries(ACTIVITY_TYPES).map(([value, label]) => ({ value, label })) },
      { prop: 'subject', label: '内容', type: 'textarea', required: true }
    ],
    initial: { type: 'VISIT' },
    onSubmit: async m => {
      await http.post('/crm/activities', { ...m, opportunityId: row.id, customerId: row.customer_id }).catch(e => { reject(e); throw e })
      resolve()
    }
  }))
}

async function newVoucher(ctx) {
  const accounts = await http.get('/gl/accounts')
  openForm({
    title: '录入手工凭证',
    width: '820px',
    fields: [
      { prop: 'date', label: '日期', type: 'date', required: true },
      { prop: 'summary', label: '摘要', required: true },
      { prop: 'lines', label: '分录（借贷必须平衡）', type: 'lines', required: true,
        columns: [
          { prop: 'account', label: '科目', type: 'select', width: 220, options: accounts.map(a => ({ value: a.code, label: `${a.code} ${a.name}` })) },
          { prop: 'debit', label: '借方' }, { prop: 'credit', label: '贷方' }] }
    ],
    initial: { date: today(), lines: [{}, {}] },
    onSubmit: async m => { await http.post('/gl/vouchers', m); ctx.ElMessage.success('凭证已保存'); ctx.reload() }
  })
}

const OPP_STAGES = { LEAD: ['线索', 'info'], QUALIFIED: ['已确认', ''], PROPOSAL: ['方案报价', ''], NEGOTIATION: ['商务谈判', 'warning'], WON: ['赢单', 'success'], LOST: ['丢单', 'danger'] }
const ACTIVITY_TYPES = { VISIT: '拜访', CALL: '电话', SAMPLE: '送样', MEETING: '技术交流', QUOTE: '报价' }
const GRADES = { A: ['A', 'success'], B: ['B', ''], C: ['C', 'warning'], D: ['D', 'danger'] }
const QUAL = { QUALIFIED: ['合格', 'success'], OBSERVATION: ['观察', 'warning'], DISQUALIFIED: ['淘汰', 'danger'] }
const RFQ_STATUS = { OPEN: ['询价中', 'warning'], AWARDED: ['已定标', 'success'], CANCELLED: ['已取消', 'info'] }

// ---------------- 菜单与列表配置 ----------------

export const MENU = [
  {
    title: '经营看板', icon: 'DataAnalysis', flat: true,
    items: [{ path: '/dashboard', title: '经营看板', roles: FIN, component: () => import('./views/Dashboard.vue') }]
  },
  {
    title: '智能问数', icon: 'MagicStick',
    items: [
      { path: '/copilot', title: '经营与人力问数', component: () => import('./views/Copilot.vue') },
      { path: '/copilot/semantic', title: '指标口径与模板', component: () => import('./views/CopilotSemantic.vue') },
      { path: '/copilot/audit', title: '问数审计', roles: ['ADMIN'], component: () => import('./views/CopilotAudit.vue') }
    ]
  },
  {
    title: '跟单工作台', icon: 'Message',
    items: [
      { path: '/workflow', title: '待办中心', component: () => import('./views/WorkflowTasks.vue') },
      { path: '/workflow/mails', title: '邮件分拣', roles: ['ADMIN', 'SALES', 'MANAGEMENT'], component: () => import('./views/WorkflowMails.vue') },
      { path: '/workflow/opportunities', title: '商机', roles: ['ADMIN', 'SALES', 'MANAGEMENT'], component: () => import('./views/WorkflowOpps.vue') },
      { path: '/workflow/rules', title: '规则与统计', roles: ['ADMIN', 'SALES', 'MANAGEMENT', 'FINANCE'], component: () => import('./views/WorkflowRules.vue') }
    ]
  },
  {
    title: '企业知识库', icon: 'Collection',
    items: [
      { path: '/kbase', title: '知识库问答', component: () => import('./views/KbaseAsk.vue') },
      { path: '/kbase/admin', title: '资料与入库管理', roles: ['ADMIN', 'ENGINEER', 'QUALITY'], component: () => import('./views/KbaseAdmin.vue') }
    ]
  },
  {
    title: '主数据', icon: 'Files',
    items: [
      { path: '/md/products', title: '产品', ...list({
        key: 'products', columns: [
          { prop: 'code', label: '型号编码', minWidth: 170 }, { prop: 'name', label: '名称', minWidth: 200 },
          { prop: 'series', label: '系列', type: 'dict', dict: 'series' }, { prop: 'business_unit', label: '事业部', minWidth: 120 },
          { prop: 'voltage', label: '电压' }, { prop: 'power_w', label: '功率W', type: 'qty' }, { prop: 'rated_speed_rpm', label: '转速rpm', type: 'int' },
          { prop: 'list_price', label: '标准售价', type: 'money' }, { prop: 'conversion_cost', label: '单位加工费', type: 'money', sensitive: true },
          { prop: 'lot_size', label: '生产批量', type: 'int' }],
        filters: [{ prop: 'series', label: '系列', options: enumOpts(SERIES) }]
      }) },
      { path: '/md/materials', title: '物料', ...list({
        key: 'materials', columns: [
          { prop: 'code', label: '物料编码', minWidth: 120 }, { prop: 'name', label: '名称', minWidth: 180 },
          { prop: 'category', label: '类别' }, { prop: 'supply_type', label: '来源', type: 'status', map: SUPPLY, width: 80 },
          { prop: 'line_code', label: '自制产线', width: 80 }, { prop: 'uom', label: '单位', width: 70 },
          { prop: 'latest_price', label: '最新参考价', type: 'money' }, { prop: 'substitute_count', label: '替代料', type: 'int', width: 70 }],
        filters: [{ prop: 'category', label: '类别', options: ['COPPER', 'STEEL', 'MAGNET', 'BEARING', 'SHAFT', 'PLASTIC', 'PCB', 'ENCODER', 'GEAR', 'PACKING', 'SEMI'].map(v => ({ value: v, label: v })) },
          { prop: 'supply_type', label: '来源', options: [{ value: 'BUY', label: '外购' }, { value: 'MAKE', label: '自制半成品' }] }]
      }) },
      { path: '/md/bom-tree', title: '多级 BOM 展开', component: () => import('./views/BomTree.vue') },
      { path: '/md/material-bom', title: '半成品 BOM', ...list({
        key: 'material-bom', columns: [
          { prop: 'parent_code', label: '半成品', minWidth: 140 }, { prop: 'parent_name', label: '半成品名称', minWidth: 200 },
          { prop: 'line_code', label: '产线', width: 70 }, { prop: 'child_code', label: '下层物料', minWidth: 120 },
          { prop: 'child_name', label: '下层物料名称', minWidth: 200 }, { prop: 'supply_type', label: '来源', type: 'status', map: SUPPLY, width: 80 },
          { prop: 'qty_per', label: '单位用量', type: 'qty' }, { prop: 'uom', label: '单位', width: 70 }]
      }) },
      { path: '/md/substitutes', title: '替代料', ...list({
        key: 'substitutes',
        toolbar: [{ label: '新增替代料', roles: ['ADMIN', 'ENGINEER', 'PURCHASE', 'PRODUCTION'], run: newSubstitute }],
        columns: [
          { prop: 'material_code', label: '主料', minWidth: 110 }, { prop: 'material_name', label: '主料名称', minWidth: 170 },
          { prop: 'substitute_code', label: '替代料', minWidth: 110 }, { prop: 'substitute_name', label: '替代料名称', minWidth: 200 },
          { prop: 'ratio', label: '替代比例', type: 'qty', width: 80 }, { prop: 'priority', label: '优先级', type: 'int', width: 70 },
          { prop: 'valid_from', label: '生效', width: 100 }, { prop: 'substitute_stock', label: '替代料库存', type: 'qty' },
          { prop: 'used_qty', label: '累计替代用量', type: 'qty' }, { prop: 'reason', label: '替代依据', minWidth: 260 }],
        actions: [{ label: '删除', roles: ['ADMIN', 'ENGINEER'], confirm: r => `删除替代关系 ${r.material_code} → ${r.substitute_code}？`,
          run: r => http.delete(`/md/substitutes/${r.id}`) }]
      }) },
      { path: '/md/bom', title: 'BOM 物料清单', ...list({
        key: 'bom', columns: [
          { prop: 'product_code', label: '产品', minWidth: 170 }, { prop: 'product_name', label: '产品名称', minWidth: 180 },
          { prop: 'material_code', label: '物料', minWidth: 110 }, { prop: 'material_name', label: '物料名称', minWidth: 160 },
          { prop: 'supply_type', label: '来源', type: 'status', map: SUPPLY, width: 80 },
          { prop: 'qty_per', label: '单台用量', type: 'qty' }, { prop: 'uom', label: '单位', width: 70 }]
      }) },
      { path: '/md/customers', title: '客户', ...list({
        key: 'customers', columns: [
          { prop: 'code', label: '编码', width: 80 }, { prop: 'name', label: '客户名称', minWidth: 220 }, { prop: 'industry', label: '行业' },
          { prop: 'region', label: '区域' }, { prop: 'country', label: '国家' }, { prop: 'is_domestic', label: '内销', type: 'bool', width: 70 },
          { prop: 'currency', label: '币种', width: 70 }, { prop: 'credit_limit', label: '信用额度', type: 'money' },
          { prop: 'payment_terms_days', label: '账期(天)', type: 'int' }],
        filters: [
          { prop: 'industry', label: '行业', options: ['家电', '机器人', '安防', '办公自动化', '医疗器械', '工业自动化'].map(v => ({ value: v, label: v })) },
          { prop: 'is_domestic', label: '内外销', options: [{ value: 1, label: '内销' }, { value: 0, label: '外销' }] }]
      }) },
      { path: '/md/suppliers', title: '供应商', ...list({
        key: 'suppliers', columns: [
          { prop: 'code', label: '编码', width: 80 }, { prop: 'name', label: '供应商名称', minWidth: 220 },
          { prop: 'category', label: '主供类别' }, { prop: 'region', label: '地区' }, { prop: 'payment_terms_days', label: '账期(天)', type: 'int' }]
      }) }
    ]
  },
  {
    title: '研发 PLM', icon: 'Cpu',
    items: [
      { path: '/plm/ecns', title: '工程变更 ECN', ...list({
        key: 'ecns', clickable: true, detail: markRaw(EcnDetail), detailTitle: '工程变更详情', dateFilter: true,
        toolbar: [{ label: '新建 ECN', roles: ['ADMIN', 'ENGINEER'], run: newEcn }],
        columns: [
          { prop: 'ecn_no', label: 'ECN 编号', minWidth: 140 }, { prop: 'title', label: '标题', minWidth: 260 },
          { prop: 'reason', label: '原因', type: 'dict', dict: 'reason', width: 90 }, { prop: 'series', label: '涉及系列', minWidth: 110 },
          { prop: 'product_count', label: '产品数', type: 'int', width: 70 }, { prop: 'requested_by', label: '申请人', width: 80 },
          { prop: 'request_date', label: '申请日期', width: 100 }, { prop: 'effective_date', label: '生效日期', width: 100 },
          { prop: 'annual_impact', label: '年度成本影响', type: 'money' }, { prop: 'pending_reviews', label: '待会签', type: 'int', width: 70 },
          { prop: 'status', label: '状态', type: 'status', map: ECN_STATUS }],
        filters: [{ prop: 'status', label: '状态', options: Object.entries(ECN_STATUS).map(([value, v]) => ({ value, label: v[0] })) },
          { prop: 'reason', label: '原因', options: enumOpts(ECN_REASONS) }]
      }) },
      { path: '/plm/bom-revisions', title: 'BOM 版本', ...list({
        key: 'bom-revisions', clickable: true, detail: markRaw(BomRevisionDetail), detailTitle: 'BOM 版本详情', dateFilter: true,
        columns: [
          { prop: 'product_code', label: '产品', minWidth: 170 }, { prop: 'product_name', label: '产品名称', minWidth: 200 },
          { prop: 'series', label: '系列', type: 'dict', dict: 'series' }, { prop: 'revision', label: '版本', width: 60 },
          { prop: 'line_count', label: '物料数', type: 'int', width: 70 }, { prop: 'effective_date', label: '生效日期', width: 100 },
          { prop: 'obsolete_date', label: '作废日期', width: 100 }, { prop: 'ecn_no', label: '来源 ECN', minWidth: 140 },
          { prop: 'status', label: '状态', type: 'status', map: REV_STATUS }],
        filters: [{ prop: 'status', label: '状态', options: [{ value: 'RELEASED', label: '生效' }, { value: 'OBSOLETE', label: '已作废' }] },
          { prop: 'series', label: '系列', options: enumOpts(SERIES) }]
      }) },
      { path: '/plm/documents', title: '技术文档', ...list({
        key: 'documents', dateFilter: true, actionWidth: 160,
        toolbar: [{ label: '上传文档', roles: ['ADMIN', 'ENGINEER', 'QUALITY', 'PRODUCTION'], run: ctx => uploadDoc(ctx) }],
        columns: [
          { prop: 'doc_no', label: '文档编号', minWidth: 130 }, { prop: 'title', label: '标题', minWidth: 240 },
          { prop: 'doc_type', label: '类型', type: 'dict', dict: 'docType', width: 100 }, { prop: 'revision', label: '版本', type: 'int', width: 60 },
          { prop: 'product_code', label: '关联产品', minWidth: 160 }, { prop: 'file_name', label: '文件', minWidth: 180 },
          { prop: 'uploaded_by', label: '上传人', width: 80 }, { prop: 'upload_date', label: '日期', width: 100 },
          { prop: 'status', label: '状态', type: 'status', map: DOC_STATUS }],
        filters: [{ prop: 'doc_type', label: '类型', options: enumOpts(DOC_TYPES) },
          { prop: 'status', label: '状态', options: [{ value: 'CURRENT', label: '现行' }, { value: 'SUPERSEDED', label: '已替代' }] }],
        actions: [
          { label: '下载', run: downloadDoc },
          { label: '升版', roles: ['ADMIN', 'ENGINEER', 'QUALITY', 'PRODUCTION'], visible: r => r.status === 'CURRENT', run: r => uploadDoc(null, r), successMsg: '已上传新版本' }]
      }) },
      { path: '/plm/projects', title: '新产品开发 NPI', ...list({
        key: 'projects', clickable: true, detail: markRaw(ProjectDetail), detailTitle: '项目阶段门', dateFilter: true,
        toolbar: [{ label: '立项', roles: ['ADMIN', 'ENGINEER'], run: newProject }],
        columns: [
          { prop: 'project_no', label: '项目编号', minWidth: 130 }, { prop: 'name', label: '项目名称', minWidth: 240 },
          { prop: 'series', label: '系列', type: 'dict', dict: 'series' }, { prop: 'stage', label: '阶段', type: 'dict', dict: 'stage', width: 70 },
          { prop: 'owner', label: '负责人', width: 80 }, { prop: 'target_customer', label: '目标客户', minWidth: 180 },
          { prop: 'start_date', label: '立项', width: 100 }, { prop: 'target_mp_date', label: '计划量产', width: 100 },
          { prop: 'budget', label: '预算', type: 'money', sensitive: true }, { prop: 'budget_used', label: '预算使用', type: 'pct', sensitive: true },
          { prop: 'status', label: '状态', type: 'status', map: PROJECT_STATUS }],
        filters: [{ prop: 'status', label: '状态', options: Object.entries(PROJECT_STATUS).map(([value, v]) => ({ value, label: v[0] })) },
          { prop: 'stage', label: '阶段', options: enumOpts(STAGES) }]
      }) }
    ]
  },
  {
    title: '销售管理', icon: 'Sell',
    items: [
      { path: '/crm/opportunities', title: '商机管道(CRM)', roles: ['ADMIN', 'SALES', 'MANAGEMENT', 'FINANCE'], ...list({
        key: 'opportunities', dateFilter: true, actionWidth: 190,
        toolbar: [{ label: '新建商机', roles: ['ADMIN', 'SALES'], run: newOpportunity }],
        columns: [
          { prop: 'opp_no', label: '商机编号', minWidth: 130 }, { prop: 'title', label: '商机', minWidth: 240 },
          { prop: 'customer_name', label: '客户', minWidth: 180 }, { prop: 'series', label: '系列', type: 'dict', dict: 'series' },
          { prop: 'amount', label: '预计年采购额', type: 'money' }, { prop: 'probability', label: '赢率%', type: 'int', width: 70 },
          { prop: 'weighted_amount', label: '加权金额', type: 'money' }, { prop: 'expected_close', label: '预计成交', width: 100 },
          { prop: 'last_activity', label: '最近跟进', width: 100 }, { prop: 'owner', label: '负责人', width: 70 },
          { prop: 'stage', label: '阶段', type: 'status', map: OPP_STAGES }, { prop: 'lost_reason', label: '丢单原因', minWidth: 160 }],
        filters: [{ prop: 'stage', label: '阶段', options: Object.entries(OPP_STAGES).map(([value, v]) => ({ value, label: v[0] })) },
          { prop: 'series', label: '系列', options: enumOpts(SERIES) }],
        actions: [
          { label: '推进', roles: ['ADMIN', 'SALES'], visible: r => NEXT_STAGE[r.stage], confirm: r => `推进到「${OPP_STAGES[NEXT_STAGE[r.stage]][0]}」？`,
            run: r => http.post(`/crm/opportunities/${r.id}/advance`, { target: NEXT_STAGE[r.stage] }) },
          { label: '丢单', roles: ['ADMIN', 'SALES'], visible: r => NEXT_STAGE[r.stage],
            run: async (r, ctx) => { const { value } = await ctx.ElMessageBox.prompt('丢单原因', '丢单', { inputValidator: v => !!v?.trim() || '必填' });
              return http.post(`/crm/opportunities/${r.id}/advance`, { target: 'LOST', lostReason: value }) } },
          { label: '跟进', roles: ['ADMIN', 'SALES'], visible: r => !['WON', 'LOST'].includes(r.stage), run: newActivity }]
      }) },
      { path: '/crm/activities', title: '跟进记录(CRM)', roles: ['ADMIN', 'SALES', 'MANAGEMENT'], ...list({
        key: 'activities', dateFilter: true,
        columns: [
          { prop: 'activity_date', label: '日期', width: 100 }, { prop: 'type', label: '方式', width: 80 },
          { prop: 'customer_name', label: '客户', minWidth: 180 }, { prop: 'opp_title', label: '商机', minWidth: 220 },
          { prop: 'subject', label: '内容', minWidth: 260 }, { prop: 'owner', label: '负责人', width: 80 }],
        filters: [{ prop: 'type', label: '方式', options: Object.entries(ACTIVITY_TYPES).map(([value, label]) => ({ value, label })) }]
      }) },
      { path: '/crm/contacts', title: '客户联系人(CRM)', roles: ['ADMIN', 'SALES', 'MANAGEMENT'], ...list({
        key: 'contacts',
        columns: [
          { prop: 'customer_name', label: '客户', minWidth: 200 }, { prop: 'name', label: '姓名' }, { prop: 'title', label: '职务' },
          { prop: 'phone', label: '电话' }, { prop: 'email', label: '邮箱', minWidth: 200 }, { prop: 'is_primary', label: '主联系人', type: 'bool', width: 90 }]
      }) },
      { path: '/sales/orders', title: '销售订单', ...list({
        key: 'sales-orders', clickable: true, detail: markRaw(SalesOrderDetail), detailTitle: '销售订单详情', dateFilter: true,
        toolbar: [{ label: '新建订单', roles: ['ADMIN', 'SALES'], run: newSalesOrder }],
        columns: [
          { prop: 'order_no', label: '订单号', minWidth: 130 }, { prop: 'customer_name', label: '客户', minWidth: 200 },
          { prop: 'industry', label: '行业' }, { prop: 'order_date', label: '下单日期', width: 100 }, { prop: 'required_date', label: '交期', width: 100 },
          { prop: 'currency', label: '币种', width: 60 }, { prop: 'amount', label: '金额(原币)', type: 'money' },
          { prop: 'amount_cny', label: '金额(人民币)', type: 'money' }, { prop: 'status', label: '状态', type: 'status' }, { prop: 'salesperson', label: '销售员' }],
        filters: [{ prop: 'status', label: '状态', options: st('DRAFT', 'CONFIRMED', 'PARTIAL', 'SHIPPED', 'CANCELLED') }]
      }) },
      { path: '/sales/deliveries', title: '发货单', ...list({
        key: 'deliveries', dateFilter: true,
        columns: [
          { prop: 'delivery_no', label: '发货单号', minWidth: 130 }, { prop: 'order_no', label: '订单号', minWidth: 130 },
          { prop: 'customer_name', label: '客户', minWidth: 200 }, { prop: 'warehouse_name', label: '发货仓', minWidth: 110 }, { prop: 'delivery_date', label: '发货日期' },
          { prop: 'amount_cny', label: '收入(人民币)', type: 'money' }, { prop: 'cost_cny', label: '成本', type: 'money', sensitive: true },
          { prop: 'status', label: '状态', type: 'status' }],
        filters: [{ prop: 'status', label: '状态', options: st('SHIPPED', 'INVOICED') }],
        actions: [{ label: '开票', roles: ['ADMIN', 'FINANCE'], visible: r => r.status === 'SHIPPED',
          run: r => http.post(`/sales/deliveries/${r.id}/invoice`, { date: today() }) }]
      }) },
      { path: '/sales/invoices', title: '应收发票', roles: [...FIN, 'SALES'], ...list({
        key: 'sales-invoices', dateFilter: true,
        columns: [
          { prop: 'invoice_no', label: '发票号', minWidth: 130 }, { prop: 'customer_name', label: '客户', minWidth: 200 },
          { prop: 'invoice_date', label: '开票日期' }, { prop: 'due_date', label: '到期日' }, { prop: 'currency', label: '币种', width: 60 },
          { prop: 'total_cny', label: '应收(含税)', type: 'money' }, { prop: 'received_cny', label: '已收', type: 'money' },
          { prop: 'outstanding_cny', label: '未收', type: 'money' }, { prop: 'overdue_days', label: '逾期天数', type: 'int' },
          { prop: 'status', label: '状态', type: 'status' }],
        filters: [{ prop: 'status', label: '状态', options: st('OPEN', 'PARTIAL', 'PAID') },
          { prop: 'industry', label: '行业', options: ['家电', '机器人', '安防', '办公自动化', '医疗器械', '工业自动化'].map(v => ({ value: v, label: v })) }],
        actions: [{ label: '收款', roles: ['ADMIN', 'FINANCE'], visible: r => r.status !== 'PAID',
          run: r => amountPrompt(`收款 - ${r.invoice_no}`, r.outstanding_cny,
            m => http.post(`/sales/invoices/${r.id}/receipts`, { ...m, method: 'BANK' })) }]
      }) },
      { path: '/sales/receipts', title: '收款记录', roles: FIN, ...list({
        key: 'receipts', dateFilter: true,
        columns: [
          { prop: 'receipt_no', label: '收款单号', minWidth: 130 }, { prop: 'customer_name', label: '客户', minWidth: 200 },
          { prop: 'invoice_no', label: '发票号', minWidth: 130 }, { prop: 'receipt_date', label: '收款日期' },
          { prop: 'amount_cny', label: '金额', type: 'money' }, { prop: 'method', label: '方式' }]
      }) }
    ]
  },
  {
    title: '采购管理', icon: 'ShoppingCart',
    items: [
      { path: '/purchase/orders', title: '采购订单', ...list({
        key: 'purchase-orders', clickable: true, detail: markRaw(PurchaseOrderDetail), detailTitle: '采购订单详情', dateFilter: true,
        toolbar: [{ label: '新建采购单', roles: ['ADMIN', 'PURCHASE'], run: newPurchaseOrder }],
        columns: [
          { prop: 'po_no', label: '采购单号', minWidth: 130 }, { prop: 'supplier_name', label: '供应商', minWidth: 200 },
          { prop: 'order_date', label: '下单日期' }, { prop: 'expected_date', label: '预计到货' },
          { prop: 'amount', label: '金额(不含税)', type: 'money' }, { prop: 'status', label: '状态', type: 'status' }, { prop: 'buyer', label: '采购员' }],
        filters: [{ prop: 'status', label: '状态', options: st('DRAFT', 'CONFIRMED', 'PARTIAL', 'RECEIVED', 'CANCELLED') }]
      }) },
      { path: '/srm/rfqs', title: '询价比价(SRM)', ...list({
        key: 'rfqs', clickable: true, detail: markRaw(RfqDetail), detailTitle: '询价比价', dateFilter: true,
        toolbar: [{ label: '新建询价', roles: ['ADMIN', 'PURCHASE'], run: newRfq }],
        columns: [
          { prop: 'rfq_no', label: '询价单号', minWidth: 130 }, { prop: 'material_code', label: '物料', minWidth: 110 },
          { prop: 'material_name', label: '物料名称', minWidth: 160 }, { prop: 'qty', label: '数量', type: 'qty' },
          { prop: 'need_date', label: '需求日', width: 100 }, { prop: 'quote_count', label: '报价数', type: 'int', width: 70 },
          { prop: 'best_price', label: '最低价', type: 'money' }, { prop: 'awarded_supplier_name', label: '中标供应商', minWidth: 180 },
          { prop: 'awarded_price', label: '中标价', type: 'money' }, { prop: 'status', label: '状态', type: 'status', map: RFQ_STATUS }],
        filters: [{ prop: 'status', label: '状态', options: [{ value: 'OPEN', label: '询价中' }, { value: 'AWARDED', label: '已定标' }] }]
      }) },
      { path: '/srm/scores', title: '供应商绩效(SRM)', ...list({
        key: 'supplier-scores', dateFilter: true,
        columns: [
          { prop: 'score_month', label: '月份', width: 100 }, { prop: 'supplier_name', label: '供应商', minWidth: 190 },
          { prop: 'category', label: '类别', width: 90 }, { prop: 'lots', label: '批次', type: 'int', width: 60 },
          { prop: 'pass_rate', label: '批合格率', type: 'pct' }, { prop: 'on_time_rate', label: '准时率', type: 'pct' },
          { prop: 'price_index', label: '价格指数', type: 'qty' }, { prop: 'quality_score', label: '质量分', type: 'qty' },
          { prop: 'delivery_score', label: '交付分', type: 'qty' }, { prop: 'price_score', label: '价格分', type: 'qty' },
          { prop: 'total_score', label: '总分', type: 'qty' }, { prop: 'grade', label: '等级', type: 'status', map: GRADES },
          { prop: 'qualification', label: '准入', type: 'status', map: QUAL }],
        filters: [{ prop: 'grade', label: '等级', options: ['A', 'B', 'C', 'D'].map(v => ({ value: v, label: v })) },
          { prop: 'qualification', label: '准入', options: Object.entries(QUAL).map(([value, v]) => ({ value, label: v[0] })) }]
      }) },
      { path: '/purchase/receipts', title: '到货单', ...list({
        key: 'goods-receipts', clickable: true, detail: markRaw(GoodsReceiptDetail), detailTitle: '到货与来料检验', dateFilter: true,
        columns: [
          { prop: 'gr_no', label: '到货单号', minWidth: 130 }, { prop: 'po_no', label: '采购单号', minWidth: 130 },
          { prop: 'supplier_name', label: '供应商', minWidth: 200 }, { prop: 'receipt_date', label: '到货日期' },
          { prop: 'accepted_amount', label: '合格金额', type: 'money' }, { prop: 'pending_lines', label: '待检行', type: 'int' },
          { prop: 'status', label: '状态', type: 'status' }],
        filters: [{ prop: 'status', label: '状态', options: st('PENDING_IQC', 'COMPLETED', 'INVOICED') }]
      }) },
      { path: '/purchase/invoices', title: '应付发票', roles: FIN, ...list({
        key: 'purchase-invoices', dateFilter: true,
        columns: [
          { prop: 'invoice_no', label: '发票号', minWidth: 130 }, { prop: 'supplier_name', label: '供应商', minWidth: 200 },
          { prop: 'invoice_date', label: '收票日期' }, { prop: 'due_date', label: '到期日' },
          { prop: 'estimate_amount', label: '暂估金额', type: 'money' }, { prop: 'amount', label: '发票金额(不含税)', type: 'money' },
          { prop: 'variance', label: '价差', type: 'money' }, { prop: 'variance_rate', label: '价差率', type: 'pct' },
          { prop: 'total', label: '应付(含税)', type: 'money' }, { prop: 'paid', label: '已付', type: 'money' },
          { prop: 'outstanding', label: '未付', type: 'money' }, { prop: 'overdue_days', label: '逾期天数', type: 'int' },
          { prop: 'status', label: '状态', type: 'status' }, { prop: 'approved_by', label: '审批人', width: 80 }],
        filters: [{ prop: 'status', label: '状态', options: st('PENDING_APPROVAL', 'OPEN', 'PARTIAL', 'PAID', 'REJECTED') }],
        actions: [
          { label: '批准价差', roles: ['ADMIN', 'FINANCE', 'MANAGEMENT'], visible: r => r.status === 'PENDING_APPROVAL',
            confirm: r => `价差 ${Number(r.variance).toFixed(2)}（${(r.variance_rate * 100).toFixed(1)}%），批准后记账：冲回暂估、确认应付，价差按批次在库比例计入存货 / 主营业务成本。确认？`,
            run: r => http.post(`/purchase/invoices/${r.id}/approve`, { date: today() }) },
          { label: '驳回', roles: ['ADMIN', 'FINANCE', 'MANAGEMENT'], visible: r => r.status === 'PENDING_APPROVAL', confirm: () => '驳回后发票作废，到货单保持暂估，等待供应商重开。确认？',
            run: r => http.post(`/purchase/invoices/${r.id}/reject`, { date: today() }) },
          { label: '付款', roles: ['ADMIN', 'FINANCE'], visible: r => ['OPEN', 'PARTIAL'].includes(r.status),
          run: r => amountPrompt(`付款 - ${r.invoice_no}`, r.outstanding, m => http.post(`/purchase/invoices/${r.id}/payments`, m)) }]
      }) },
      { path: '/purchase/price-variance', title: '采购价差', roles: FIN, ...list({
        key: 'price-variances', dateFilter: true,
        columns: [
          { prop: 'invoice_no', label: '发票号', minWidth: 130 }, { prop: 'invoice_date', label: '收票日期', width: 100 },
          { prop: 'supplier_name', label: '供应商', minWidth: 190 }, { prop: 'material_code', label: '物料', minWidth: 110 },
          { prop: 'material_name', label: '物料名称', minWidth: 160 }, { prop: 'qty', label: '数量', type: 'qty' },
          { prop: 'po_price', label: '订单价', type: 'money' }, { prop: 'invoice_price', label: '发票价', type: 'money' },
          { prop: 'variance', label: '价差', type: 'money' }, { prop: 'variance_rate', label: '价差率', type: 'pct' },
          { prop: 'stock_ratio', label: '在库比例', type: 'pct' }, { prop: 'variance_stock', label: '计入存货', type: 'money' },
          { prop: 'variance_cogs', label: '计入成本', type: 'money' }, { prop: 'status', label: '发票状态', type: 'status' }],
        filters: [{ prop: 'category', label: '类别', options: ['COPPER', 'MAGNET'].map(v => ({ value: v, label: v })) },
          { prop: 'status', label: '发票状态', options: st('PENDING_APPROVAL', 'OPEN', 'PARTIAL', 'PAID', 'REJECTED') }]
      }) },
      { path: '/purchase/payments', title: '付款记录', roles: FIN, ...list({
        key: 'payments', dateFilter: true,
        columns: [
          { prop: 'payment_no', label: '付款单号', minWidth: 130 }, { prop: 'supplier_name', label: '供应商', minWidth: 200 },
          { prop: 'invoice_no', label: '发票号', minWidth: 130 }, { prop: 'payment_date', label: '付款日期' }, { prop: 'amount', label: '金额', type: 'money' }]
      }) }
    ]
  },
  {
    title: '库存管理', icon: 'Box',
    items: [
      { path: '/inventory/stock', title: '现存量', ...list({
        key: 'stock',
        columns: [
          { prop: 'warehouse_name', label: '仓库' }, { prop: 'item_code', label: '编码', minWidth: 170 }, { prop: 'item_name', label: '名称', minWidth: 200 },
          { prop: 'item_group', label: '分类' }, { prop: 'qty', label: '现存量', type: 'qty' },
          { prop: 'avg_cost', label: '平均成本', type: 'money', sensitive: true }, { prop: 'stock_value', label: '库存金额', type: 'money', sensitive: true }],
        filters: [{ prop: 'item_type', label: '类型', options: [{ value: 'MATERIAL', label: '原材料 / 半成品' }, { value: 'PRODUCT', label: '成品' }] },
          { prop: 'warehouse_code', label: '仓库', options: WAREHOUSES }]
      }) },
      { path: '/inventory/warehouses', title: '仓库', ...list({
        key: 'warehouses',
        columns: [
          { prop: 'code', label: '编码', width: 100 }, { prop: 'name', label: '仓库', minWidth: 160 },
          { prop: 'type', label: '类型', type: 'status', map: WH_TYPES, width: 90 }, { prop: 'region', label: '服务区域', width: 90 },
          { prop: 'transit_days', label: '调拨在途(天)', type: 'int' }, { prop: 'sku_count', label: '在库品种', type: 'int' },
          { prop: 'total_qty', label: '总数量', type: 'qty' }, { prop: 'stock_value', label: '库存金额', type: 'money', sensitive: true },
          { prop: 'in_transit_value', label: '在途调入金额', type: 'money', sensitive: true }]
      }) },
      { path: '/inventory/transfers', title: '调拨单', ...list({
        key: 'transfers', clickable: true, detail: markRaw(TransferDetail), detailTitle: '调拨单', dateFilter: true,
        toolbar: [{ label: '新建调拨', roles: ['ADMIN', 'WAREHOUSE'], run: newTransfer },
          { label: '前置仓补货建议', roles: ['ADMIN', 'WAREHOUSE'], run: transfersFromSuggestions }],
        columns: [
          { prop: 'transfer_no', label: '调拨单号', minWidth: 130 }, { prop: 'from_warehouse', label: '调出仓', minWidth: 110 },
          { prop: 'to_warehouse', label: '调入仓', minWidth: 140 }, { prop: 'create_date', label: '创建', width: 100 },
          { prop: 'ship_date', label: '发出', width: 100 }, { prop: 'expected_date', label: '预计到货', width: 100 },
          { prop: 'receive_date', label: '到货', width: 100 }, { prop: 'line_count', label: '行数', type: 'int', width: 60 },
          { prop: 'total_qty', label: '数量', type: 'qty' }, { prop: 'amount', label: '金额', type: 'money', sensitive: true },
          { prop: 'status', label: '状态', type: 'status', map: TR_STATUS }, { prop: 'remark', label: '备注', minWidth: 160 }],
        filters: [{ prop: 'status', label: '状态', options: Object.entries(TR_STATUS).map(([value, v]) => ({ value, label: v[0] })) }]
      }) },
      { path: '/wms/bin-stock', title: '库位库存(WMS)', ...list({
        key: 'bin-stock',
        toolbar: [{ label: '账实一致性校验', run: async ctx => { const r = await http.get('/wms/reconcile');
          r.length ? ctx.ElMessageBox.alert(`${r.length} 个物料库位汇总与现存量不一致`, '校验结果') : ctx.ElMessage.success('全部一致：Σ库位库存 = 仓库现存量') } }],
        columns: [
          { prop: 'warehouse_name', label: '仓库' }, { prop: 'zone', label: '库区' }, { prop: 'location_code', label: '库位' },
          { prop: 'item_code', label: '编码', minWidth: 160 }, { prop: 'item_name', label: '名称', minWidth: 200 }, { prop: 'qty', label: '数量', type: 'qty' }],
        filters: [{ prop: 'item_type', label: '类型', options: [{ value: 'MATERIAL', label: '原材料' }, { value: 'PRODUCT', label: '成品' }] }]
      }) },
      { path: '/wms/tasks', title: '仓库作业(WMS)', ...list({
        key: 'wms-tasks', dateFilter: true,
        columns: [
          { prop: 'task_date', label: '日期', width: 100 }, { prop: 'task_type', label: '作业', width: 90 },
          { prop: 'item_code', label: '编码', minWidth: 160 }, { prop: 'item_name', label: '名称', minWidth: 180 },
          { prop: 'from_location', label: '源库位' }, { prop: 'to_location', label: '目标库位' }, { prop: 'qty', label: '数量', type: 'qty' },
          { prop: 'ref_no', label: '来源单据', minWidth: 130 }],
        filters: [{ prop: 'task_type', label: '作业', options: [{ value: 'PUTAWAY', label: '上架' }, { value: 'PICK', label: '拣货' }, { value: 'MOVE', label: '移库' }] }]
      }) },
      { path: '/inventory/lots', title: '批次', ...list({
        key: 'lots', dateFilter: true,
        columns: [
          { prop: 'lot_no', label: '批次号', minWidth: 140 }, { prop: 'warehouse_name', label: '仓库', minWidth: 110 }, { prop: 'item_code', label: '编码', minWidth: 150 },
          { prop: 'item_name', label: '名称', minWidth: 180 }, { prop: 'source_type', label: '来源', width: 120 },
          { prop: 'supplier_name', label: '供应商', minWidth: 170 }, { prop: 'iqc_result', label: 'IQC', type: 'status', width: 80 },
          { prop: 'received_date', label: '入库日期', width: 100 }, { prop: 'qty_in', label: '入库数量', type: 'qty' },
          { prop: 'qty_remaining', label: '剩余', type: 'qty' }],
        filters: [{ prop: 'item_type', label: '类型', options: [{ value: 'MATERIAL', label: '原材料' }, { value: 'PRODUCT', label: '成品' }] }]
      }) },
      { path: '/wms/locations', title: '库位(WMS)', ...list({
        key: 'locations',
        columns: [{ prop: 'warehouse_name', label: '仓库' }, { prop: 'zone', label: '库区' }, { prop: 'code', label: '库位' },
          { prop: 'sku_count', label: '存放品种', type: 'int' }, { prop: 'total_qty', label: '总数量', type: 'qty' }]
      }) },
      { path: '/inventory/txns', title: '库存流水', ...list({
        key: 'inventory-txns', dateFilter: true,
        columns: [
          { prop: 'txn_date', label: '日期', width: 100 }, { prop: 'txn_type', label: '类型', type: 'dict', dict: 'txn' },
          { prop: 'item_code', label: '编码', minWidth: 160 }, { prop: 'item_name', label: '名称', minWidth: 180 },
          { prop: 'qty', label: '数量', type: 'qty' }, { prop: 'unit_cost', label: '单位成本', type: 'money', sensitive: true },
          { prop: 'amount', label: '金额', type: 'money', sensitive: true }, { prop: 'ref_no', label: '来源单据', minWidth: 130 }],
        filters: [{ prop: 'txn_type', label: '类型', options: enumOpts(TXN_TYPES) }]
      }) }
    ]
  },
  {
    title: '生产管理', icon: 'SetUp',
    items: [
      { path: '/production/work-orders', title: '生产工单', ...list({
        key: 'work-orders', dateFilter: true, actionWidth: 170,
        toolbar: [{ label: '新建工单', roles: ['ADMIN', 'PRODUCTION'], run: newWorkOrder }],
        columns: [
          { prop: 'wo_no', label: '工单号', minWidth: 130 }, { prop: 'item_type', label: '类型', type: 'status', map: WO_TYPES, width: 80 },
          { prop: 'product_code', label: '产品 / 组件', minWidth: 170 }, { prop: 'parent_wo_no', label: '上级工单', minWidth: 130 },
          { prop: 'line_code', label: '产线', width: 60 }, { prop: 'planned_qty', label: '计划数', type: 'qty' },
          { prop: 'good_qty', label: '良品', type: 'qty' }, { prop: 'scrap_qty', label: '报废', type: 'qty' },
          { prop: 'yield_rate', label: '良率', type: 'pct' }, { prop: 'plan_start', label: '计划开工', width: 100 },
          { prop: 'material_cost', label: '材料成本', type: 'money', sensitive: true }, { prop: 'status', label: '状态', type: 'status' }],
        filters: [{ prop: 'status', label: '状态', options: st('PLANNED', 'RELEASED', 'IN_PROGRESS', 'COMPLETED') },
          { prop: 'item_type', label: '类型', options: [{ value: 'PRODUCT', label: '成品工单' }, { value: 'MATERIAL', label: '组件工单' }] },
          { prop: 'line_code', label: '产线', options: ['L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8'].map(v => ({ value: v, label: v })) }],
        actions: [
          { label: '下达', roles: ['ADMIN', 'PRODUCTION'], visible: r => r.status === 'PLANNED', run: r => http.post(`/production/work-orders/${r.id}/release`) },
          { label: '报工', roles: ['ADMIN', 'PRODUCTION'], visible: r => ['RELEASED', 'IN_PROGRESS'].includes(r.status), run: reportWork, successMsg: '报工成功，已倒冲领料并完工入库' },
          { label: '关闭', roles: ['ADMIN', 'PRODUCTION'], visible: r => r.status === 'IN_PROGRESS', confirm: r => `确认短关闭工单 ${r.wo_no}？`,
            run: r => http.post(`/production/work-orders/${r.id}/close`) }]
      }) },
      { path: '/aps', title: '高级排产(APS)', component: () => import('./views/ApsView.vue') },
      { path: '/mes/board', title: '车间看板(MES)', component: () => import('./views/MesBoard.vue') },
      { path: '/mes/line-days', title: '产线日报(OEE)', ...list({
        key: 'line-days', dateFilter: true,
        columns: [
          { prop: 'work_date', label: '日期', width: 100 }, { prop: 'line_code', label: '产线', width: 60 }, { prop: 'good_qty', label: '良品', type: 'qty' },
          { prop: 'capacity', label: '理论产能', type: 'int' }, { prop: 'loss_min', label: '停机损失(分)', type: 'int' },
          { prop: 'availability', label: '时间开动率', type: 'pct' }, { prop: 'performance', label: '性能开动率', type: 'pct' },
          { prop: 'quality', label: '合格品率', type: 'pct' }, { prop: 'oee', label: 'OEE', type: 'pct' },
          { prop: 'std_hours', label: '标准工时', type: 'qty' }, { prop: 'actual_hours', label: '出勤工时', type: 'qty' }, { prop: 'labor_efficiency', label: '劳动效率', type: 'pct' }],
        filters: [{ prop: 'line_code', label: '产线', options: LINES }]
      }) },
      { path: '/mes/andons', title: '安灯', ...list({
        key: 'andons', dateFilter: true,
        columns: [
          { prop: 'raised_at', label: '呼叫时间', width: 150 }, { prop: 'line_code', label: '产线', width: 60 }, { prop: 'type', label: '类型', type: 'status', map: ANDON_TYPES, width: 70 },
          { prop: 'description', label: '描述', minWidth: 260 }, { prop: 'ref_no', label: '单据', minWidth: 130 },
          { prop: 'response_min', label: '响应(分)', type: 'int', width: 80 }, { prop: 'resolve_min', label: '解决(分)', type: 'int', width: 80 }, { prop: 'status', label: '状态', type: 'status' }],
        filters: [{ prop: 'type', label: '类型', options: Object.entries(ANDON_TYPES).map(([value, v]) => ({ value, label: v[0] })) },
          { prop: 'status', label: '状态', options: [{ value: 'OPEN', label: '未关闭' }, { value: 'RESOLVED', label: '已解决' }] }, { prop: 'line_code', label: '产线', options: LINES }],
        actions: [{ label: '关闭', visible: r => r.status === 'OPEN', run: r => http.post(`/mes/andons/${r.id}/resolve`) }]
      }) },
      { path: '/mes/ipqc', title: '首检巡检(IPQC)', ...list({
        key: 'ipqc', dateFilter: true,
        columns: [
          { prop: 'inspect_time', label: '时间', width: 150 }, { prop: 'wo_no', label: '工单', minWidth: 130 }, { prop: 'line_code', label: '产线', width: 60 },
          { prop: 'type', label: '类型', type: 'status', map: { FIRST: ['首检', ''], PATROL: ['巡检', 'info'] }, width: 70 },
          { prop: 'inspector', label: '检验员', width: 90 }, { prop: 'result', label: '结果', type: 'status', width: 70 }, { prop: 'remark', label: '备注', minWidth: 200 }],
        filters: [{ prop: 'type', label: '类型', options: [{ value: 'FIRST', label: '首检' }, { value: 'PATROL', label: '巡检' }] },
          { prop: 'result', label: '结果', options: st('PASS', 'FAIL') }, { prop: 'line_code', label: '产线', options: LINES }]
      }) },
      { path: '/mes/serials', title: '整机序列号', ...list({
        key: 'serials', clickable: true, detail: markRaw(SerialDetail), detailTitle: '整机追溯', dateFilter: true,
        columns: [
          { prop: 'sn', label: '序列号', minWidth: 150 }, { prop: 'product_code', label: '产品', minWidth: 140 }, { prop: 'wo_no', label: '工单批次', minWidth: 130 },
          { prop: 'produced_date', label: '生产日期', width: 100 }, { prop: 'result', label: '测试', type: 'status', width: 70 }, { prop: 'fail_item', label: '不合格项', width: 110 },
          { prop: 'back_emf', label: '反电势', type: 'qty' }, { prop: 'encoder_offset', label: '零位偏差', type: 'qty' }, { prop: 'vibration', label: '振动', type: 'qty' },
          { prop: 'delivery_no', label: '发货单', minWidth: 130 }, { prop: 'customer_name', label: '客户', minWidth: 170 }],
        filters: [{ prop: 'result', label: '测试', options: st('PASS', 'FAIL') }]
      }) },
      { path: '/mes/routings', title: '工艺路线', ...list({
        key: 'routings',
        columns: [
          { prop: 'item_key', label: '适用', width: 170 }, { prop: 'op_seq', label: '序', type: 'int', width: 50 }, { prop: 'op_code', label: '工序', width: 70 },
          { prop: 'op_name', label: '名称', width: 120 }, { prop: 'line_code', label: '产线', width: 60 }, { prop: 'std_seconds', label: '标准工时(秒)', type: 'qty' },
          { prop: 'is_key', label: '关键工序', type: 'bool', width: 80 }, { prop: 'inspect_type', label: '检验', width: 80 }],
        filters: [{ prop: 'line_code', label: '产线', options: LINES }]
      }) },
      { path: '/production/substitutions', title: '替代料使用', ...list({
        key: 'substitutions', dateFilter: true,
        columns: [
          { prop: 'txn_date', label: '日期', width: 100 }, { prop: 'wo_no', label: '工单', minWidth: 130 },
          { prop: 'product_code', label: '产品 / 组件', minWidth: 160 }, { prop: 'material_code', label: '主料', minWidth: 110 },
          { prop: 'material_name', label: '主料名称', minWidth: 160 }, { prop: 'substitute_code', label: '替代料', minWidth: 110 },
          { prop: 'substitute_name', label: '替代料名称', minWidth: 200 }, { prop: 'primary_qty', label: '替代主料数量', type: 'qty' },
          { prop: 'substitute_qty', label: '耗用替代料', type: 'qty' }]
      }) },
      { path: '/production/reports', title: '报工记录', ...list({
        key: 'wo-reports', dateFilter: true,
        columns: [
          { prop: 'report_date', label: '日期', width: 100 }, { prop: 'wo_no', label: '工单号', minWidth: 130 }, { prop: 'line_code', label: '产线', width: 60 },
          { prop: 'product_code', label: '产品', minWidth: 170 }, { prop: 'good_qty', label: '良品', type: 'qty' },
          { prop: 'scrap_qty', label: '报废', type: 'qty' }, { prop: 'defect_code', label: '主要不良', type: 'dict', dict: 'defect' }, { prop: 'operator', label: '班组' }],
        filters: [{ prop: 'line_code', label: '产线', options: ['L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8'].map(v => ({ value: v, label: v })) }]
      }) }
    ]
  },
  {
    title: '设备管理', icon: 'Tools',
    items: [
      { path: '/eam/dashboard', title: '设备看板', component: () => import('./views/EamDashboard.vue') },
      { path: '/eam/equipment', title: '设备台账', ...list({
        key: 'equipment', clickable: true, detail: markRaw(EquipmentDetail), detailTitle: '设备档案与可靠性', dateFilter: true,
        columns: [
          { prop: 'code', label: '编码', minWidth: 140 }, { prop: 'name', label: '名称', minWidth: 150 }, { prop: 'line_code', label: '产线', width: 60 },
          { prop: 'category', label: '类别', width: 90 }, { prop: 'criticality', label: '关键度', width: 70 }, { prop: 'loss_factor', label: '停机损失', type: 'pct', width: 80 },
          { prop: 'purchase_date', label: '购置', width: 100 }, { prop: 'purchase_cost', label: '原值', type: 'money', sensitive: true },
          { prop: 'breakdowns', label: '累计故障', type: 'int', width: 80 }, { prop: 'breakdown_min', label: '故障停机(分)', type: 'int' },
          { prop: 'next_pm', label: '下次保养', width: 100 }, { prop: 'status', label: '状态', type: 'status', map: EQ_STATUS, width: 70 }],
        filters: [{ prop: 'line_code', label: '产线', options: LINES }, { prop: 'criticality', label: '关键度', options: ['A', 'B', 'C'].map(v => ({ value: v, label: v })) }]
      }) },
      { path: '/eam/work-orders', title: '维修保养工单', ...list({
        key: 'eam-work-orders', dateFilter: true,
        toolbar: [{ label: '设备报修', run: reportBreakdown }],
        columns: [
          { prop: 'wo_no', label: '单号', minWidth: 130 }, { prop: 'type', label: '类型', type: 'status', map: EAM_TYPES, width: 90 },
          { prop: 'equipment_code', label: '设备', minWidth: 140 }, { prop: 'equipment_name', label: '名称', minWidth: 140 }, { prop: 'line_code', label: '产线', width: 60 },
          { prop: 'reported_at', label: '报修时间', width: 150 }, { prop: 'fault_code', label: '代码', width: 60 }, { prop: 'fault_desc', label: '现象', minWidth: 140 },
          { prop: 'cause', label: '原因', minWidth: 140 }, { prop: 'downtime_min', label: '停机(分)', type: 'int', width: 80 },
          { prop: 'spare_cost', label: '备件', type: 'money', sensitive: true }, { prop: 'technician', label: '技术员', width: 80 },
          { prop: 'status', label: '状态', type: 'status', map: EAM_STATUS, width: 80 }],
        filters: [{ prop: 'type', label: '类型', options: Object.entries(EAM_TYPES).map(([value, v]) => ({ value, label: v[0] })) },
          { prop: 'line_code', label: '产线', options: LINES }, { prop: 'status', label: '状态', options: Object.entries(EAM_STATUS).map(([value, v]) => ({ value, label: v[0] })) }],
        actions: [
          { label: '开始维修', roles: ['ADMIN', 'PRODUCTION'], visible: r => r.status === 'OPEN', run: r => http.post(`/eam/work-orders/${r.id}/start`) },
          { label: '完工', roles: ['ADMIN', 'PRODUCTION'], visible: r => r.status === 'IN_PROGRESS', run: completeRepair }]
      }) },
      { path: '/eam/pm-plans', title: '保养计划', ...list({
        key: 'pm-plans', dateFilter: true,
        columns: [
          { prop: 'equipment_code', label: '设备', minWidth: 140 }, { prop: 'equipment_name', label: '名称', minWidth: 140 }, { prop: 'line_code', label: '产线', width: 60 },
          { prop: 'name', label: '计划', width: 100 }, { prop: 'interval_days', label: '周期(天)', type: 'int', width: 80 }, { prop: 'est_minutes', label: '停机(分)', type: 'int', width: 80 },
          { prop: 'last_done', label: '上次', width: 100 }, { prop: 'next_due', label: '下次', width: 100 }, { prop: 'checklist', label: '保养内容', minWidth: 200 }],
        filters: [{ prop: 'line_code', label: '产线', options: LINES }],
        actions: [
          { label: '执行保养', roles: ['ADMIN', 'PRODUCTION'], run: r => http.post(`/eam/pm-plans/${r.id}/do`) },
          { label: '调整周期', roles: ['ADMIN', 'PRODUCTION'], run: async (r, ctx) => {
            const { value } = await ctx.ElMessageBox.prompt('新保养周期（天）', r.equipment_name, { inputValue: String(r.interval_days), inputPattern: /^\d+$/ })
            await http.post(`/eam/pm-plans/${r.id}/interval`, { intervalDays: Number(value) }) } }]
      }) }
    ]
  },
  {
    title: '质量管理', icon: 'CircleCheck',
    items: [
      { path: '/quality/iqc', title: '来料检验', ...list({
        key: 'iqc', dateFilter: true,
        columns: [
          { prop: 'iqc_no', label: '检验单号', minWidth: 130 }, { prop: 'inspect_date', label: '检验日期', width: 100 },
          { prop: 'supplier_name', label: '供应商', minWidth: 190 }, { prop: 'material_name', label: '物料', minWidth: 160 },
          { prop: 'lot_qty', label: '批量', type: 'qty' }, { prop: 'sample_qty', label: '抽样', type: 'int' }, { prop: 'defect_qty', label: '不良', type: 'int' },
          { prop: 'result', label: '结果', type: 'status' }, { prop: 'defect_desc', label: '不良描述' }],
        filters: [{ prop: 'result', label: '结果', options: st('PASS', 'FAIL') },
          { prop: 'category', label: '类别', options: ['COPPER', 'STEEL', 'MAGNET', 'BEARING', 'SHAFT', 'PLASTIC', 'PCB', 'ENCODER', 'GEAR', 'PACKING'].map(v => ({ value: v, label: v })) }]
      }) },
      { path: '/quality/trace', title: '批次追溯', component: () => import('./views/Trace.vue') },
      { path: '/quality/complaints', title: '客户投诉(8D)', ...list({
        key: 'complaints', dateFilter: true, actionWidth: 160,
        toolbar: [{ label: '登记客诉', roles: ['ADMIN', 'QUALITY', 'SALES'], run: newComplaint }],
        columns: [
          { prop: 'complaint_no', label: '客诉单号', minWidth: 130 }, { prop: 'received_date', label: '接收日期', width: 100 },
          { prop: 'customer_name', label: '客户', minWidth: 190 }, { prop: 'product_code', label: '产品', minWidth: 160 },
          { prop: 'defect_type', label: '不良类型', type: 'dict', dict: 'defect' }, { prop: 'qty_affected', label: '数量', type: 'qty' },
          { prop: 'severity', label: '严重度', type: 'status' }, { prop: 'status', label: '状态', type: 'status' },
          { prop: 'root_cause', label: '根本原因', minWidth: 220 }],
        filters: [{ prop: 'status', label: '状态', options: st('OPEN', 'ANALYZING', 'CLOSED') },
          { prop: 'defect_type', label: '不良类型', options: enumOpts(DEFECTS) }],
        actions: [
          { label: '开始分析', roles: ['ADMIN', 'QUALITY'], visible: r => r.status === 'OPEN', run: r => http.put(`/quality/complaints/${r.id}`, { status: 'ANALYZING' }) },
          { label: '8D 关闭', roles: ['ADMIN', 'QUALITY'], visible: r => r.status === 'ANALYZING', run: closeComplaint },
          { label: '追溯', visible: r => !!r.delivery_id, run: r => { window.location.href = `/quality/trace?mode=complaint&key=${r.complaint_no}`; return false } }]
      }) },
      { path: '/quality/8d', title: '8D 报告', ...list({
        key: 'eight-d', clickable: true, detail: markRaw(EightDDetail), detailTitle: '8D 报告', dateFilter: true,
        columns: [
          { prop: 'report_no', label: '8D 编号', minWidth: 130 }, { prop: 'complaint_no', label: '客诉', minWidth: 130 }, { prop: 'customer_name', label: '客户', minWidth: 170 },
          { prop: 'product_code', label: '产品', minWidth: 150 }, { prop: 'defect_type', label: '不良', type: 'dict', dict: 'defect', width: 80 },
          { prop: 'severity', label: '严重度', type: 'status', width: 70 }, { prop: 'current_step', label: '步骤', type: 'int', width: 60 },
          { prop: 'opened_date', label: '开立', width: 100 }, { prop: 'due_date', label: '期限', width: 100 }, { prop: 'closed_date', label: '结案', width: 100 },
          { prop: 'age_days', label: '天数', type: 'int', width: 60 }, { prop: 'd4_root_cause', label: '根本原因', minWidth: 220 },
          { prop: 'status', label: '状态', type: 'status', map: { OPEN: ['进行中', 'warning'], CLOSED: ['已结案', 'success'] }, width: 80 }],
        filters: [{ prop: 'status', label: '状态', options: [{ value: 'OPEN', label: '进行中' }, { value: 'CLOSED', label: '已结案' }] },
          { prop: 'severity', label: '严重度', options: st('HIGH', 'MEDIUM') }]
      }) },
      { path: '/quality/spc', title: 'SPC 控制图', component: () => import('./views/SpcView.vue') },
      { path: '/quality/fmea', title: 'PFMEA', ...list({
        key: 'fmea',
        columns: [
          { prop: 'series', label: '系列', type: 'dict', dict: 'series', width: 90 }, { prop: 'process_step', label: '工序', width: 100 },
          { prop: 'failure_mode', label: '失效模式', minWidth: 150 }, { prop: 'effect', label: '影响', minWidth: 140 }, { prop: 'cause', label: '原因', minWidth: 140 },
          { prop: 'current_control', label: '现行控制', minWidth: 120 }, { prop: 'severity', label: 'S', type: 'int', width: 40 }, { prop: 'occurrence', label: 'O', type: 'int', width: 40 },
          { prop: 'detection', label: 'D', type: 'int', width: 40 }, { prop: 'rpn', label: 'RPN', type: 'int', width: 60 }, { prop: 'action', label: '改进措施', minWidth: 200 },
          { prop: 'revised_rpn', label: '改进后 RPN', type: 'int', width: 90 }, { prop: 'updated_date', label: '更新', width: 100 }],
        filters: [{ prop: 'series', label: '系列', options: enumOpts(SERIES) }]
      }) },
      { path: '/quality/gauges', title: '量具校准', ...list({
        key: 'gauges', dateFilter: true,
        columns: [
          { prop: 'code', label: '编号', width: 90 }, { prop: 'name', label: '名称', minWidth: 140 }, { prop: 'type', label: '类型', width: 90 },
          { prop: 'line_code', label: '产线', width: 60 }, { prop: 'range_spec', label: '量程', width: 120 }, { prop: 'cal_interval_days', label: '周期(天)', type: 'int', width: 80 },
          { prop: 'last_cal_date', label: '上次校准', width: 100 }, { prop: 'next_due', label: '到期', width: 100 }, { prop: 'cal_count', label: '校准次数', type: 'int', width: 80 },
          { prop: 'status', label: '状态', type: 'status', map: GAUGE_STATUS, width: 70 }],
        filters: [{ prop: 'status', label: '状态', options: Object.entries(GAUGE_STATUS).map(([value, v]) => ({ value, label: v[0] })) }],
        actions: [{ label: '校准', roles: ['ADMIN', 'QUALITY'], visible: r => r.status === 'OK', run: async (r, ctx) => {
          const { value } = await ctx.ElMessageBox.prompt('校准结果：PASS / ADJUSTED / FAIL', `校准 ${r.code} ${r.name}`, { inputValue: 'PASS', inputPattern: /^(PASS|ADJUSTED|FAIL)$/ })
          await http.post(`/qms/gauges/${r.id}/calibrate`, { result: value }) } }]
      }) },
      { path: '/quality/calibrations', title: '校准记录', ...list({
        key: 'calibrations', dateFilter: true,
        columns: [
          { prop: 'cal_date', label: '校准日期', width: 100 }, { prop: 'gauge_code', label: '量具', width: 90 }, { prop: 'gauge_name', label: '名称', minWidth: 140 },
          { prop: 'due_date', label: '应校日期', width: 100 }, { prop: 'late_days', label: '逾期(天)', type: 'int', width: 80 }, { prop: 'result', label: '结果', width: 90 },
          { prop: 'deviation', label: '偏差', minWidth: 160 }, { prop: 'certificate_no', label: '证书号', minWidth: 150 }],
        filters: [{ prop: 'result', label: '结果', options: ['PASS', 'ADJUSTED', 'FAIL'].map(v => ({ value: v, label: v })) }]
      }) },
      { path: '/quality/oqc', title: '出货检验(OQC)', ...list({
        key: 'oqc', dateFilter: true,
        toolbar: [{ label: '抽样方案查询', run: aqlPlan }],
        columns: [
          { prop: 'inspect_date', label: '日期', width: 100 }, { prop: 'delivery_no', label: '发货单', minWidth: 130 }, { prop: 'customer_name', label: '客户', minWidth: 170 },
          { prop: 'product_code', label: '产品', minWidth: 150 }, { prop: 'lot_size', label: '批量', type: 'int' }, { prop: 'aql', label: 'AQL', type: 'qty', width: 60 },
          { prop: 'code_letter', label: '字码', width: 60 }, { prop: 'sample_size', label: '样本', type: 'int', width: 70 },
          { prop: 'ac', label: 'Ac', type: 'int', width: 50 }, { prop: 're', label: 'Re', type: 'int', width: 50 }, { prop: 'defects', label: '不良', type: 'int', width: 60 },
          { prop: 'result', label: '结果', type: 'status', map: { PASS: ['合格', 'success'], REWORK: ['判退返工', 'danger'] }, width: 90 }],
        filters: [{ prop: 'result', label: '结果', options: [{ value: 'PASS', label: '合格' }, { value: 'REWORK', label: '判退返工' }] }]
      }) }
    ]
  },
  {
    title: '财务管理', icon: 'Money',
    items: [
      { path: '/gl/vouchers', title: '记账凭证', roles: FIN, ...list({
        key: 'vouchers', clickable: true, detail: markRaw(VoucherDetail), detailTitle: '凭证', dateFilter: true,
        toolbar: [{ label: '手工凭证', roles: ['ADMIN', 'FINANCE'], run: newVoucher }],
        columns: [
          { prop: 'voucher_no', label: '凭证号', minWidth: 130 }, { prop: 'voucher_date', label: '日期', width: 100 },
          { prop: 'source_type', label: '来源', width: 140 }, { prop: 'source_no', label: '来源单据', minWidth: 130 },
          { prop: 'summary', label: '摘要', minWidth: 220 }, { prop: 'total_debit', label: '借方合计', type: 'money' },
          { prop: 'total_credit', label: '贷方合计', type: 'money' }, { prop: 'line_count', label: '分录', type: 'int', width: 60 }],
        filters: [{ prop: 'source_type', label: '来源', options: ['SALES_INVOICE', 'RECEIPT', 'DELIVERY', 'GOODS_RECEIPT', 'PURCHASE_INVOICE', 'PAYMENT', 'WORK_ORDER', 'TRANSFER', 'EXPENSE', 'REIMBURSE', 'PAYROLL', 'FA', 'TAX', 'BANK', 'EAM', 'ADJUST', 'CLOSING', 'MANUAL'].map(v => ({ value: v, label: v })) }]
      }) },
      { path: '/gl/reports', title: '总账报表', roles: FIN, component: () => import('./views/GlReports.vue') },
      { path: '/gl/periods', title: '月结', roles: FIN, component: () => import('./views/PeriodClose.vue') },
      { path: '/fa/assets', title: '固定资产', ...list({
        key: 'fa-assets', clickable: true, detail: markRaw(FaAssetDetail), detailTitle: '固定资产卡片', dateFilter: true,
        toolbar: [{ label: '资本性支出申请', run: newCapex },
          { label: '计提折旧', roles: ['ADMIN', 'FINANCE'], run: async ctx => { const { value } = await ctx.ElMessageBox.prompt('期间（YYYY-MM）', '计提折旧', { inputValue: new Date().toISOString().slice(0, 7) })
            const r = await http.post(`/fa/depreciation/${value}`); ctx.ElMessage.success(`${r.assets} 项资产计提 ${money(r.amount)}`); ctx.reload() } }],
        columns: [
          { prop: 'asset_no', label: '编号', minWidth: 120 }, { prop: 'name', label: '名称', minWidth: 170 }, { prop: 'category_name', label: '类别', width: 110 },
          { prop: 'dept_name', label: '使用部门', width: 110 }, { prop: 'in_service_date', label: '投用', width: 100 },
          { prop: 'original_cost', label: '原值', type: 'money', sensitive: true }, { prop: 'accumulated_dep', label: '累计折旧', type: 'money', sensitive: true },
          { prop: 'net_value', label: '净值', type: 'money', sensitive: true }, { prop: 'dep_progress', label: '折旧进度', type: 'pct', width: 90 },
          { prop: 'status', label: '状态', type: 'status', map: { IN_USE: ['在用', 'success'], DISPOSED: ['已处置', 'info'] }, width: 70 }],
        filters: [{ prop: 'status', label: '状态', options: [{ value: 'IN_USE', label: '在用' }, { value: 'DISPOSED', label: '已处置' }] },
          { prop: 'cost_type', label: '费用归集', options: [['PRODUCTION', '制造费用'], ['ADMIN', '管理费用'], ['SALES', '销售费用'], ['RND', '研发费用']].map(([value, label]) => ({ value, label })) }]
      }) },
      { path: '/fa/depreciation', title: '折旧明细', roles: FIN, ...list({
        key: 'fa-depreciation',
        columns: [{ prop: 'period', label: '期间', width: 90 }, { prop: 'asset_no', label: '资产', minWidth: 120 }, { prop: 'asset_name', label: '名称', minWidth: 170 },
          { prop: 'category_name', label: '类别', width: 110 }, { prop: 'dept_name', label: '部门', width: 110 }, { prop: 'amount', label: '折旧额', type: 'money' },
          { prop: 'accumulated_after', label: '累计', type: 'money' }, { prop: 'expense_account', label: '费用科目', width: 90 }],
        filters: [{ prop: 'expense_account', label: '科目', options: [['5101', '制造费用'], ['6601', '销售费用'], ['6602', '管理费用'], ['6604', '研发费用']].map(([value, label]) => ({ value, label })) }]
      }) },
      { path: '/treasury/recon', title: '银行对账', roles: FIN, component: () => import('./views/BankRecon.vue') },
      { path: '/treasury/forecast', title: '资金预测', roles: FIN, component: () => import('./views/CashForecast.vue') },
      { path: '/treasury/budget', title: '预算执行', roles: FIN, component: () => import('./views/BudgetView.vue') },
      { path: '/tax/vat', title: '增值税申报', roles: FIN, ...list({
        key: 'vat-returns',
        toolbar: [{ label: '增值税月结', roles: ['ADMIN', 'FINANCE'], run: vatReturn }],
        columns: [
          { prop: 'period', label: '期间', width: 90 }, { prop: 'output_vat', label: '内销销项', type: 'money' }, { prop: 'input_vat', label: '进项', type: 'money' },
          { prop: 'prev_credit', label: '上期留抵', type: 'money' }, { prop: 'export_fob', label: '出口 FOB', type: 'money' }, { prop: 'payable', label: '应纳', type: 'money' },
          { prop: 'exempt_refund', label: '免抵退', type: 'money' }, { prop: 'refund', label: '应退', type: 'money' }, { prop: 'exempt_offset', label: '免抵', type: 'money' },
          { prop: 'end_credit', label: '期末留抵', type: 'money' }, { prop: 'surtax', label: '附加税', type: 'money' },
          { prop: 'paid_date', label: '缴纳', width: 100 }, { prop: 'refund_received', label: '退税到账', width: 100 }],
        actions: [{ label: '缴纳', roles: ['ADMIN', 'FINANCE'], visible: r => !r.paid_date, run: r => http.post(`/customs/vat/${r.period}/pay`) }]
      }) },
      { path: '/finance/expenses', title: '费用报销', ...list({
        key: 'expenses', dateFilter: true,
        toolbar: [{ label: '提交报销', run: newExpense }],
        columns: [
          { prop: 'expense_no', label: '报销单号', minWidth: 130 }, { prop: 'expense_date', label: '日期', width: 100 },
          { prop: 'dept', label: '部门' }, { prop: 'category', label: '类别' }, { prop: 'amount', label: '金额', type: 'money' },
          { prop: 'employee', label: '报销人' }, { prop: 'description', label: '事由', minWidth: 160 },
          { prop: 'status', label: '状态', type: 'status' }, { prop: 'instance_no', label: '审批流程', minWidth: 130 },
          { prop: 'pending_with', label: '当前审批人', minWidth: 100 }, { prop: 'approved_by', label: '终审人' }],
        filters: [{ prop: 'status', label: '状态', options: st('SUBMITTED', 'APPROVED', 'REJECTED') },
          { prop: 'dept', label: '部门', options: ['销售部', '采购部', '生产部', '质量部', '研发中心', '财务部', '行政部'].map(v => ({ value: v, label: v })) }]
      }) }
    ]
  },
  {
    title: '合同与关务', icon: 'Document',
    items: [
      { path: '/contracts', title: '合同台账', ...list({
        key: 'contracts', dateFilter: true,
        toolbar: [{ label: '起草合同', run: newContract }],
        columns: [
          { prop: 'contract_no', label: '合同号', minWidth: 130 }, { prop: 'type', label: '类型', type: 'status', map: { SALES: ['销售', 'success'], PURCHASE: ['采购', ''], SERVICE: ['服务', 'info'], NDA: ['保密', 'warning'] }, width: 70 },
          { prop: 'counterparty_name', label: '相对方', minWidth: 190 }, { prop: 'title', label: '标题', minWidth: 180 }, { prop: 'amount', label: '金额', type: 'money' },
          { prop: 'fulfilled_amount', label: '履约金额', type: 'money' }, { prop: 'start_date', label: '开始', width: 100 }, { prop: 'end_date', label: '结束', width: 100 },
          { prop: 'days_to_expiry', label: '距到期(天)', type: 'int', width: 90 }, { prop: 'owner_name', label: '负责人', width: 80 },
          { prop: 'status', label: '状态', type: 'status', map: { DRAFT: ['草稿', 'info'], APPROVING: ['审批中', 'warning'], ACTIVE: ['生效', 'success'], EXPIRED: ['到期', 'info'], TERMINATED: ['终止', 'danger'], REJECTED: ['驳回', 'danger'] }, width: 80 }],
        filters: [{ prop: 'type', label: '类型', options: [['SALES', '销售'], ['PURCHASE', '采购'], ['SERVICE', '服务'], ['NDA', '保密']].map(([value, label]) => ({ value, label })) },
          { prop: 'status', label: '状态', options: [['ACTIVE', '生效'], ['APPROVING', '审批中'], ['EXPIRED', '到期'], ['REJECTED', '驳回']].map(([value, label]) => ({ value, label })) }]
      }) },
      { path: '/customs/declarations', title: '出口报关单', ...list({
        key: 'declarations', dateFilter: true,
        columns: [
          { prop: 'decl_no', label: '报关单号', minWidth: 130 }, { prop: 'delivery_no', label: '发货单', minWidth: 130 }, { prop: 'customer_name', label: '客户', minWidth: 170 },
          { prop: 'country', label: '国家', width: 70 }, { prop: 'port', label: '口岸', width: 90 }, { prop: 'currency', label: '币种', width: 60 },
          { prop: 'fob_amount', label: 'FOB', type: 'money' }, { prop: 'fob_cny', label: 'FOB 人民币', type: 'money' }, { prop: 'refundable', label: '可退税额', type: 'money' },
          { prop: 'declare_date', label: '申报', width: 100 }, { prop: 'export_date', label: '结关', width: 100 }, { prop: 'refund_period', label: '退税期间', width: 90 },
          { prop: 'status', label: '状态', type: 'status', map: { DECLARED: ['已申报', 'info'], RELEASED: ['已放行', 'warning'], EXPORTED: ['已结关', 'success'] }, width: 80 }],
        filters: [{ prop: 'status', label: '状态', options: [['DECLARED', '已申报'], ['RELEASED', '已放行'], ['EXPORTED', '已结关']].map(([value, label]) => ({ value, label })) }],
        actions: [{ label: '放行', roles: ['ADMIN', 'SALES', 'WAREHOUSE'], visible: r => r.status === 'DECLARED', run: r => http.post(`/customs/declarations/${r.id}/release`) },
          { label: '结关', roles: ['ADMIN', 'SALES', 'WAREHOUSE'], visible: r => r.status === 'RELEASED', run: r => http.post(`/customs/declarations/${r.id}/export`) }]
      }) }
    ]
  },
  {
    title: '售后服务', icon: 'Service',
    items: [
      { path: '/aftersales/dashboard', title: '售后看板', component: () => import('./views/AfterSalesDashboard.vue') },
      { path: '/aftersales/tickets', title: '服务工单', ...list({
        key: 'svc-tickets', dateFilter: true,
        toolbar: [{ label: '新建工单', roles: ['ADMIN', 'SALES', 'QUALITY'], run: newServiceTicket }],
        columns: [
          { prop: 'ticket_no', label: '工单号', minWidth: 130 }, { prop: 'created_at', label: '受理时间', width: 150 }, { prop: 'customer_name', label: '客户', minWidth: 160 },
          { prop: 'product_code', label: '产品', minWidth: 130 }, { prop: 'sn', label: '序列号', minWidth: 130 },
          { prop: 'type', label: '类型', type: 'status', map: { FAULT: ['故障', 'danger'], CONSULT: ['咨询', 'info'], INSTALL: ['安装', ''] }, width: 70 },
          { prop: 'priority', label: '优先级', type: 'status', map: PRIO, width: 70 }, { prop: 'symptom', label: '现象', minWidth: 180 },
          { prop: 'warranty_status', label: '保修', type: 'status', map: { IN: ['保内', 'success'], OUT: ['保外', 'warning'], NA: ['-', 'info'] }, width: 70 },
          { prop: 'engineer_name', label: '工程师', width: 80 }, { prop: 'response_hours', label: '响应(h)', type: 'qty', width: 80 }, { prop: 'resolve_hours', label: '解决(h)', type: 'qty', width: 80 },
          { prop: 'resolution', label: '处理', type: 'status', map: { REMOTE: ['远程', 'info'], ONSITE: ['上门', ''], RMA: ['返厂', 'warning'], NO_FAULT: ['无故障', 'success'] }, width: 70 },
          { prop: 'sla_breached', label: 'SLA', type: 'status', map: { 0: ['达成', 'success'], 1: ['超时', 'danger'], true: ['超时', 'danger'], false: ['达成', 'success'] }, width: 70 },
          { prop: 'service_fee', label: '服务费', type: 'money' }, { prop: 'satisfaction', label: '满意度', width: 70 },
          { prop: 'status', label: '状态', type: 'status', map: { NEW: ['新建', 'info'], ASSIGNED: ['处理中', 'warning'], RESOLVED: ['已解决', ''], CLOSED: ['已关闭', 'success'] }, width: 80 }],
        filters: [{ prop: 'priority', label: '优先级', options: opts([['P1', 'P1'], ['P2', 'P2'], ['P3', 'P3']]) },
          { prop: 'warranty_status', label: '保修', options: opts([['IN', '保内'], ['OUT', '保外']]) },
          { prop: 'fault_code', label: '故障', options: opts(Object.entries(FAULTS)) }, { prop: 'series', label: '系列', options: enumOpts(SERIES) }]
      }) },
      { path: '/aftersales/rma', title: 'RMA 退换货', ...list({
        key: 'svc-rma', dateFilter: true,
        columns: [
          { prop: 'rma_no', label: 'RMA 号', minWidth: 130 }, { prop: 'created_date', label: '开单', width: 100 },
          { prop: 'source', label: '来源', type: 'status', map: { TICKET: ['服务工单', ''], COMPLAINT: ['客诉', 'danger'] }, width: 80 },
          { prop: 'ticket_no', label: '工单', minWidth: 130 }, { prop: 'complaint_no', label: '客诉', minWidth: 130 }, { prop: 'customer_name', label: '客户', minWidth: 160 },
          { prop: 'product_code', label: '产品', minWidth: 130 }, { prop: 'sn', label: '序列号', minWidth: 130 }, { prop: 'qty', label: '数量', type: 'qty', width: 80 },
          { prop: 'in_warranty', label: '保修', type: 'status', map: { 1: ['保内', 'success'], 0: ['保外', 'warning'], true: ['保内', 'success'], false: ['保外', 'warning'] }, width: 70 },
          { prop: 'received_date', label: '收货', width: 100 }, { prop: 'disposition', label: '处置', type: 'status', map: { REPLACE: ['换新', 'warning'], REPAIR: ['维修', ''], NO_FAULT: ['无故障', 'success'] }, width: 70 },
          { prop: 'finding', label: '检验结论', minWidth: 200 }, { prop: 'cost', label: '耗用成本', type: 'money' }, { prop: 'claim_amount', label: '客户索赔', type: 'money' },
          { prop: 'status', label: '状态', type: 'status', map: { OPEN: ['待退回', 'info'], RECEIVED: ['已收货', 'warning'], CLOSED: ['已处理', 'success'] }, width: 80 }],
        filters: [{ prop: 'source', label: '来源', options: opts([['TICKET', '服务工单'], ['COMPLAINT', '客诉']]) },
          { prop: 'status', label: '状态', options: opts([['OPEN', '待退回'], ['RECEIVED', '已收货'], ['CLOSED', '已处理']]) }],
        actions: [{ label: '收货', roles: ['ADMIN', 'QUALITY', 'WAREHOUSE'], visible: r => r.status === 'OPEN', run: r => http.post(`/aftersales/rma/${r.id}/receive`) }]
      }) },
      { path: '/aftersales/warranty-ledger', title: '质量保证金', roles: FIN, ...list({
        key: 'warranty-ledger', dateFilter: true,
        columns: [
          { prop: 'entry_date', label: '日期', width: 100 }, { prop: 'period', label: '期间', width: 80 },
          { prop: 'type', label: '类型', type: 'status', map: { OPENING: ['期初', 'info'], ACCRUAL: ['计提', ''], TOP_UP: ['补提', 'danger'], CLAIM: ['耗用', 'warning'], TRUE_UP: ['年末调整', 'danger'] }, width: 90 },
          { prop: 'amount', label: '金额', type: 'money' }, { prop: 'balance', label: '余额', type: 'money' }, { prop: 'rate', label: '计提比例', width: 90 },
          { prop: 'ref_no', label: '单据', minWidth: 130 }, { prop: 'note', label: '说明', minWidth: 260 }],
        filters: [{ prop: 'type', label: '类型', options: opts([['ACCRUAL', '计提'], ['TOP_UP', '补提'], ['CLAIM', '耗用'], ['TRUE_UP', '年末调整']]) }]
      }) }
    ]
  },
  {
    title: 'IT 服务台', icon: 'Monitor',
    items: [
      { path: '/itsm/dashboard', title: 'IT 服务看板', roles: ['ADMIN', 'MANAGEMENT'], component: () => import('./views/ItDeskDashboard.vue') },
      { path: '/itsm/tickets', title: 'IT 工单', ...list({
        key: 'it-tickets', dateFilter: true,
        toolbar: [{ label: '提交 IT 工单', run: newItTicket }],
        columns: [
          { prop: 'ticket_no', label: '工单号', minWidth: 130 }, { prop: 'created_at', label: '提交时间', width: 150 },
          { prop: 'type', label: '类型', type: 'status', map: { INCIDENT: ['故障', 'danger'], REQUEST: ['请求', ''], CHANGE: ['变更', 'warning'] }, width: 70 },
          { prop: 'category', label: '分类', width: 90 }, { prop: 'title', label: '标题', minWidth: 220 }, { prop: 'priority', label: '优先级', type: 'status', map: PRIO, width: 70 },
          { prop: 'requester_name', label: '提交人', width: 80 }, { prop: 'requester_dept', label: '部门', width: 100 }, { prop: 'assignee_name', label: '处理人', width: 80 },
          { prop: 'ci_no', label: '配置项', width: 90 }, { prop: 'resolve_hours', label: '解决(h)', type: 'qty', width: 80 },
          { prop: 'sla_breached', label: 'SLA', type: 'status', map: { 0: ['达成', 'success'], 1: ['超时', 'danger'], true: ['超时', 'danger'], false: ['达成', 'success'] }, width: 70 },
          { prop: 'risk', label: '变更风险', width: 80 }, { prop: 'root_cause', label: '根因', minWidth: 180 },
          { prop: 'status', label: '状态', type: 'status', map: { NEW: ['新建', 'info'], IN_PROGRESS: ['处理中', 'warning'], RESOLVED: ['已解决', ''], CLOSED: ['已关闭', 'success'],
            APPROVING: ['审批中', 'warning'], APPROVED: ['已批准', ''], REJECTED: ['驳回', 'danger'] }, width: 80 }],
        filters: [{ prop: 'type', label: '类型', options: opts([['INCIDENT', '故障'], ['REQUEST', '服务请求'], ['CHANGE', '变更']]) },
          { prop: 'priority', label: '优先级', options: opts([['P1', 'P1'], ['P2', 'P2'], ['P3', 'P3'], ['P4', 'P4']]) },
          { prop: 'status', label: '状态', options: opts([['NEW', '新建'], ['IN_PROGRESS', '处理中'], ['RESOLVED', '已解决'], ['CLOSED', '已关闭']]) }],
        actions: [{ label: '受理', roles: ['ADMIN'], visible: r => r.status === 'NEW', run: r => http.post(`/itsm/tickets/${r.id}/assign`) }]
      }) },
      { path: '/itsm/cmdb', title: '配置项 CMDB', ...list({
        key: 'it-cis', dateFilter: false,
        columns: [
          { prop: 'ci_no', label: '编号', width: 90 }, { prop: 'type', label: '类型', width: 90 }, { prop: 'name', label: '名称', minWidth: 160 }, { prop: 'model', label: '型号', minWidth: 150 },
          { prop: 'owner_name', label: '使用人', width: 80 }, { prop: 'dept_name', label: '部门', width: 110 }, { prop: 'location', label: '位置', minWidth: 110 },
          { prop: 'purchase_date', label: '购置', width: 100 }, { prop: 'age_months', label: '月龄', type: 'int', width: 70 },
          { prop: 'criticality', label: '重要性', type: 'status', map: { HIGH: ['高', 'danger'], MEDIUM: ['中', 'warning'], LOW: ['低', 'info'] }, width: 70 },
          { prop: 'asset_no', label: '固定资产', minWidth: 120 },
          { prop: 'status', label: '状态', type: 'status', map: { IN_USE: ['在用', 'success'], STOCK: ['库存', 'info'], REPAIR: ['维修', 'warning'], RETIRED: ['报废', 'danger'] }, width: 70 }],
        filters: [{ prop: 'type', label: '类型', options: opts([['LAPTOP', '笔记本'], ['DESKTOP', '台式机'], ['TERMINAL', 'MES 终端'], ['SERVER', '服务器'], ['NETWORK', '网络'], ['PRINTER', '打印机']]) },
          { prop: 'status', label: '状态', options: opts([['IN_USE', '在用'], ['STOCK', '库存'], ['RETIRED', '报废']]) }]
      }) },
      { path: '/itsm/licenses', title: '软件许可', ...list({
        key: 'it-licenses', dateFilter: false,
        columns: [{ prop: 'name', label: '软件', minWidth: 180 }, { prop: 'vendor', label: '厂商', minWidth: 140 }, { prop: 'scope', label: '适用范围', width: 90 },
          { prop: 'seats', label: '席位', type: 'int', width: 70 }, { prop: 'used', label: '已用', type: 'int', width: 70 }, { prop: 'available', label: '可用', type: 'int', width: 70 },
          { prop: 'unit_cost', label: '单价/年', type: 'money' }, { prop: 'annual_cost', label: '年费', type: 'money' }, { prop: 'renew_date', label: '续费日', width: 100 }]
      }) }
    ]
  },
  {
    title: '安环能源', icon: 'FirstAidKit',
    items: [
      { path: '/ehs/dashboard', title: '安环看板', component: () => import('./views/EhsDashboard.vue') },
      { path: '/ehs/hazards', title: '隐患排查', ...list({
        key: 'ehs-hazards', dateFilter: true,
        toolbar: [{ label: '随手拍', run: reportHazard }],
        columns: [
          { prop: 'hazard_no', label: '编号', minWidth: 130 }, { prop: 'found_date', label: '发现', width: 100 },
          { prop: 'source', label: '来源', type: 'status', map: { INSPECTION: ['周检', ''], REPORT: ['随手拍', 'success'], AUDIT: ['专项', 'warning'], INCIDENT: ['事故整改', 'danger'] }, width: 80 },
          { prop: 'dept_name', label: '部门', width: 110 }, { prop: 'location', label: '位置', width: 100 }, { prop: 'category', label: '类别', width: 80 },
          { prop: 'level', label: '等级', type: 'status', map: { GENERAL: ['一般', 'info'], MAJOR: ['重大', 'danger'] }, width: 70 }, { prop: 'description', label: '描述', minWidth: 240 },
          { prop: 'owner_name', label: '责任人', width: 80 }, { prop: 'deadline', label: '期限', width: 100 }, { prop: 'rectified_date', label: '整改', width: 100 },
          { prop: 'overdue', label: '逾期', type: 'status', map: { 1: ['逾期', 'danger'], 0: ['', 'info'] }, width: 70 }, { prop: 'action', label: '整改措施', minWidth: 200 },
          { prop: 'status', label: '状态', type: 'status', map: { OPEN: ['待整改', 'danger'], RECTIFIED: ['待复查', 'warning'], CLOSED: ['已关闭', 'success'] }, width: 80 }],
        filters: [{ prop: 'status', label: '状态', options: opts([['OPEN', '待整改'], ['RECTIFIED', '待复查'], ['CLOSED', '已关闭']]) },
          { prop: 'level', label: '等级', options: opts([['GENERAL', '一般'], ['MAJOR', '重大']]) },
          { prop: 'category', label: '类别', options: ['电气', '机械', '消防', '化学品', '叉车', '作业环境', '职业健康', '能源'].map(v => ({ value: v, label: v })) }],
        actions: [{ label: '复查通过', roles: ['ADMIN', 'PRODUCTION', 'MANAGEMENT'], visible: r => r.status === 'RECTIFIED', run: r => http.post(`/ehs/hazards/${r.id}/verify`) }]
      }) },
      { path: '/ehs/incidents', title: '事故与未遂', ...list({
        key: 'ehs-incidents', dateFilter: true,
        columns: [
          { prop: 'incident_no', label: '编号', minWidth: 130 }, { prop: 'occurred_at', label: '发生时间', width: 150 }, { prop: 'dept_name', label: '部门', width: 110 },
          { prop: 'location', label: '地点', width: 100 },
          { prop: 'type', label: '类型', type: 'status', map: { NEAR_MISS: ['未遂', 'info'], FIRST_AID: ['急救', ''], RECORDABLE: ['可记录', 'warning'], LOST_TIME: ['损工', 'danger'] }, width: 80 },
          { prop: 'description', label: '经过', minWidth: 220 }, { prop: 'employee_name', label: '当事人', width: 80 }, { prop: 'injury', label: '伤害', minWidth: 120 },
          { prop: 'lost_days', label: '损失工日', type: 'int', width: 80 }, { prop: 'root_cause', label: '根因', minWidth: 200 }, { prop: 'corrective_action', label: '整改措施', minWidth: 220 },
          { prop: 'status', label: '状态', type: 'status', map: { OPEN: ['调查中', 'warning'], CLOSED: ['已关闭', 'success'] }, width: 80 }],
        filters: [{ prop: 'type', label: '类型', options: opts([['NEAR_MISS', '未遂'], ['FIRST_AID', '急救'], ['RECORDABLE', '可记录'], ['LOST_TIME', '损工']]) }]
      }) },
      { path: '/ehs/permits', title: '作业票', ...list({
        key: 'ehs-permits', dateFilter: true,
        columns: [
          { prop: 'permit_no', label: '编号', minWidth: 130 }, { prop: 'type', label: '作业类型', type: 'status', map: { HOT_WORK: ['动火', 'danger'], HEIGHT: ['高处', 'warning'], CONFINED: ['受限空间', 'danger'], ELECTRICAL: ['临时用电', ''] }, width: 90 },
          { prop: 'level', label: '等级', width: 60 }, { prop: 'location', label: '地点', width: 120 }, { prop: 'work_desc', label: '作业内容', minWidth: 180 },
          { prop: 'applicant_name', label: '申请人', width: 80 }, { prop: 'guardian_name', label: '监护人', width: 80 }, { prop: 'planned_start', label: '开始', width: 150 },
          { prop: 'planned_end', label: '结束', width: 150 }, { prop: 'closed_at', label: '关闭', width: 150 },
          { prop: 'status', label: '状态', type: 'status', map: { APPROVING: ['审批中', 'warning'], APPROVED: ['已批准', ''], REJECTED: ['驳回', 'danger'], CLOSED: ['已关闭', 'success'] }, width: 80 }],
        filters: [{ prop: 'type', label: '类型', options: opts([['HOT_WORK', '动火'], ['HEIGHT', '高处'], ['CONFINED', '受限空间'], ['ELECTRICAL', '临时用电']]) }]
      }) },
      { path: '/ehs/waste', title: '危废台账', ...list({
        key: 'ehs-waste', dateFilter: true,
        columns: [
          { prop: 'move_date', label: '日期', width: 100 }, { prop: 'waste_code', label: '类别', width: 70 }, { prop: 'waste_name', label: '名称', minWidth: 180 },
          { prop: 'move_type', label: '类型', type: 'status', map: { GENERATE: ['产生', 'warning'], TRANSFER: ['转移', 'success'] }, width: 70 },
          { prop: 'qty_ton', label: '数量(吨)', type: 'qty', width: 90 }, { prop: 'dept_name', label: '产生部门', width: 110 }, { prop: 'manifest_no', label: '联单号', minWidth: 130 },
          { prop: 'receiver', label: '接收单位', minWidth: 190 }, { prop: 'cost', label: '处置费', type: 'money' }],
        filters: [{ prop: 'waste_code', label: '类别', options: opts([['HW08', 'HW08 废矿物油'], ['HW12', 'HW12 浸漆废物'], ['HW49', 'HW49 其他废物']]) },
          { prop: 'move_type', label: '类型', options: opts([['GENERATE', '产生'], ['TRANSFER', '转移']]) }]
      }) },
      { path: '/energy/dashboard', title: '能源看板', roles: ['ADMIN', 'PRODUCTION', 'MANAGEMENT', 'FINANCE'], component: () => import('./views/EnergyDashboard.vue') },
      { path: '/energy/bills', title: '电费账单', ...list({
        key: 'energy-bills', dateFilter: false,
        columns: [
          { prop: 'period', label: '期间', width: 80 }, { prop: 'total_kwh', label: '电量(kWh)', type: 'qty' }, { prop: 'peak_kwh', label: '峰', type: 'qty' },
          { prop: 'sharp_kwh', label: '尖峰', type: 'qty' }, { prop: 'flat_kwh', label: '平', type: 'qty' }, { prop: 'valley_kwh', label: '谷', type: 'qty' },
          { prop: 'energy_charge', label: '电度电费', type: 'money' }, { prop: 'capacity_charge', label: '基本电费', type: 'money' }, { prop: 'water_charge', label: '水费', type: 'money' },
          { prop: 'total_amount', label: '合计', type: 'money' }, { prop: 'avg_price', label: '综合电价', width: 90 }, { prop: 'kwh_per_unit', label: 'kWh/台', width: 90 },
          { prop: 'carbon_t', label: '碳排放(t)', type: 'qty', width: 100 }]
      }) }
    ]
  },
  {
    title: '研发协作', icon: 'Share',
    items: [
      { path: '/pm/board', title: '研发项目看板', component: () => import('./views/PmBoard.vue') },
      { path: '/pm/issues', title: '问题检索', ...list({
        key: 'pm-issues', dateFilter: true,
        columns: [
          { prop: 'issue_key', label: '编号', width: 90 }, { prop: 'project_name', label: '项目', minWidth: 150 },
          { prop: 'type', label: '类型', type: 'status', map: { EPIC: ['史诗', 'warning'], STORY: ['需求', 'success'], TASK: ['任务', ''], BUG: ['缺陷', 'danger'] }, width: 70 },
          { prop: 'title', label: '标题', minWidth: 220 }, { prop: 'status', label: '状态', type: 'status', map: { TODO: ['待办', 'info'], IN_PROGRESS: ['进行中', ''], IN_REVIEW: ['评审中', 'warning'], DONE: ['完成', 'success'] }, width: 80 },
          { prop: 'priority', label: '优先级', width: 80 }, { prop: 'assignee_name', label: '经办人', width: 80 }, { prop: 'sprint_name', label: '迭代', width: 90 },
          { prop: 'story_points', label: '点', type: 'int', width: 50 }, { prop: 'cycle_days', label: '周期(天)', type: 'qty', width: 80 },
          { prop: 'logged_hours', label: '工时', type: 'qty', width: 70 }, { prop: 'complaint_no', label: '客诉', minWidth: 120 }, { prop: 'ecn_no', label: 'ECN', minWidth: 120 }],
        filters: [{ prop: 'type', label: '类型', options: [['EPIC', '史诗'], ['STORY', '需求'], ['TASK', '任务'], ['BUG', '缺陷']].map(([value, label]) => ({ value, label })) },
          { prop: 'status', label: '状态', options: [['TODO', '待办'], ['IN_PROGRESS', '进行中'], ['IN_REVIEW', '评审中'], ['DONE', '完成']].map(([value, label]) => ({ value, label })) },
          { prop: 'project_key', label: '项目', options: ['JM', 'DH', 'BJ', 'VF', 'LX', 'SV', 'BL', 'TE'].map(v => ({ value: v, label: v })) }]
      }) },
      { path: '/pm/worklogs', title: '研发工时', ...list({
        key: 'pm-worklogs', dateFilter: true,
        columns: [
          { prop: 'work_date', label: '日期', width: 100 }, { prop: 'emp_name', label: '人员', width: 90 }, { prop: 'project_key', label: '项目', width: 70 },
          { prop: 'issue_key', label: '问题', width: 90 }, { prop: 'issue_title', label: '标题', minWidth: 220 }, { prop: 'hours', label: '小时', type: 'qty', width: 70 },
          { prop: 'comment', label: '说明', minWidth: 140 }]
      }) },
      { path: '/pm/rnd-cost', title: '研发费用归集', roles: FIN, component: () => import('./views/RndCost.vue') },
      { path: '/kb', title: '知识库', component: () => import('./views/KbView.vue') }
    ]
  },
  {
    title: '协同办公', icon: 'Stamp',
    items: [
      { path: '/oa/center', title: '审批中心', component: () => import('./views/OaCenter.vue') },
      { path: '/oa/notices', title: '公告', component: () => import('./views/Notices.vue') },
      { path: '/oa/instances', title: '全部流程', roles: ['ADMIN', 'HR', 'MANAGEMENT', 'FINANCE'], ...list({
        key: 'wf-instances', clickable: true, detail: markRaw(WfDetail), detailTitle: '审批详情', dateFilter: true,
        columns: [
          { prop: 'instance_no', label: '流程号', minWidth: 130 }, { prop: 'def_name', label: '类型', width: 100 },
          { prop: 'title', label: '标题', minWidth: 260 }, { prop: 'applicant_name', label: '申请人', width: 90 },
          { prop: 'applicant_dept', label: '部门', width: 110 }, { prop: 'started_at', label: '发起时间', width: 150 },
          { prop: 'duration_hours', label: '耗时(h)', type: 'int', width: 80 }, { prop: 'pending_with', label: '当前审批人', width: 110 },
          { prop: 'status', label: '状态', type: 'status' }],
        filters: [{ prop: 'def_code', label: '类型', options: [['LEAVE', '请假'], ['EXPENSE', '费用报销'], ['TRAVEL', '出差'], ['SEAL', '用印'], ['RESIGN', '离职'], ['PAYROLL', '工资发放'], ['GENERAL', '通用审批']].map(([value, label]) => ({ value, label })) },
          { prop: 'status', label: '状态', options: st('RUNNING', 'APPROVED', 'REJECTED', 'WITHDRAWN') }]
      }) },
      { path: '/oa/definitions', title: '流程定义', roles: ['ADMIN', 'HR', 'MANAGEMENT'], component: () => import('./views/WfDefinitions.vue') }
    ]
  },
  {
    title: '组织人事', icon: 'User',
    items: [
      { path: '/hr/org', title: '组织架构', component: () => import('./views/OrgTree.vue') },
      { path: '/hr/roster', title: '员工花名册', roles: ['ADMIN', 'HR', 'MANAGEMENT', 'FINANCE'], ...list({
        key: 'employee-roster',
        columns: [
          { prop: 'emp_no', label: '工号', width: 90 }, { prop: 'name', label: '姓名', width: 80 }, { prop: 'gender', label: '性别', width: 56 },
          { prop: 'age', label: '年龄', width: 56 }, { prop: 'dept_name', label: '部门', minWidth: 110 }, { prop: 'position_name', label: '职务', minWidth: 110 },
          { prop: 'grade', label: '职级', width: 60 }, { prop: 'education', label: '学历', width: 60 }, { prop: 'school', label: '毕业院校', minWidth: 110 },
          { prop: 'hire_date', label: '入职日期', width: 100 }, { prop: 'status', label: '状态', type: 'status', width: 70 },
          { prop: 'email', label: '邮箱', minWidth: 200 }, { prop: 'work_location', label: '办公地点', width: 100 },
          { prop: 'phone_masked', label: '手机号', width: 115 }, { prop: 'phone', label: '手机号（明文）', width: 115, sensitive: true },
          { prop: 'id_card_masked', label: '身份证号', width: 170 }, { prop: 'id_card', label: '身份证号（明文）', width: 170, sensitive: true },
          { prop: 'address_masked', label: '家庭住址', minWidth: 170 }, { prop: 'home_address', label: '家庭住址（明文）', minWidth: 260, sensitive: true },
          { prop: 'emergency_contact', label: '紧急联系人', width: 90 }, { prop: 'emergency_relation', label: '关系', width: 56 },
          { prop: 'emergency_phone_masked', label: '紧急联系电话', width: 115 }, { prop: 'emergency_phone', label: '紧急联系电话（明文）', width: 130, sensitive: true },
          { prop: 'native_place', label: '籍贯', width: 90 }, { prop: 'marital_status', label: '婚姻', width: 56 }],
        filters: [{ prop: 'status', label: '状态', options: st('ACTIVE', 'PROBATION', 'LEFT') },
          { prop: 'work_location', label: '办公地点', options: ['深圳总部', '祁阳生产基地'].map(v => ({ value: v, label: v })) },
          { prop: 'education', label: '学历', options: ['博士', '硕士', '本科', '大专', '中专', '高中'].map(v => ({ value: v, label: v })) }]
      }) },
      { path: '/hr/dashboard', title: 'HR 看板', roles: ['ADMIN', 'HR', 'MANAGEMENT', 'FINANCE'], component: () => import('./views/HrDashboard.vue') },
      { path: '/hr/changes', title: '员工异动', roles: ['ADMIN', 'HR', 'MANAGEMENT'], ...list({
        key: 'employee-changes', dateFilter: true,
        columns: [
          { prop: 'change_date', label: '日期', width: 100 }, { prop: 'emp_no', label: '工号', width: 90 }, { prop: 'name', label: '姓名', width: 90 },
          { prop: 'change_type', label: '类型', type: 'status', map: CHANGE_TYPES, width: 80 }, { prop: 'from_dept', label: '原部门' }, { prop: 'to_dept', label: '新部门' },
          { prop: 'from_salary', label: '原工资', type: 'money', sensitive: true }, { prop: 'to_salary', label: '新工资', type: 'money', sensitive: true },
          { prop: 'remark', label: '说明', minWidth: 200 }],
        filters: [{ prop: 'change_type', label: '类型', options: Object.entries(CHANGE_TYPES).map(([value, v]) => ({ value, label: v[0] })) }]
      }) },
      { path: '/hr/attendance', title: '考勤', roles: ['ADMIN', 'HR', 'MANAGEMENT'], ...list({
        key: 'attendance', dateFilter: true,
        columns: [
          { prop: 'work_date', label: '日期', width: 100 }, { prop: 'emp_no', label: '工号', width: 90 }, { prop: 'name', label: '姓名', width: 90 },
          { prop: 'dept_name', label: '部门', minWidth: 110 }, { prop: 'line_code', label: '产线', width: 60 }, { prop: 'team', label: '班组', width: 60 },
          { prop: 'status', label: '状态', type: 'status', width: 70 }, { prop: 'clock_in', label: '上班', width: 90 }, { prop: 'clock_out', label: '下班', width: 90 },
          { prop: 'work_hours', label: '工时', type: 'qty', width: 60 }, { prop: 'overtime_hours', label: '加班', type: 'qty', width: 60 }, { prop: 'leave_type', label: '假别', width: 80 }],
        filters: [{ prop: 'status', label: '状态', options: st('NORMAL', 'LATE', 'ABSENT', 'LEAVE') },
          { prop: 'line_code', label: '产线', options: ['L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8'].map(v => ({ value: v, label: v })) }]
      }) },
      { path: '/hr/leaves', title: '请假', roles: ['ADMIN', 'HR', 'MANAGEMENT'], ...list({
        key: 'leaves', dateFilter: true,
        columns: [
          { prop: 'emp_no', label: '工号', width: 90 }, { prop: 'name', label: '姓名', width: 90 }, { prop: 'dept_name', label: '部门', minWidth: 110 },
          { prop: 'leave_type', label: '假别', type: 'status', map: LEAVE_TYPES, width: 70 }, { prop: 'start_date', label: '开始', width: 100 },
          { prop: 'end_date', label: '结束', width: 100 }, { prop: 'days', label: '天数', type: 'qty', width: 60 }, { prop: 'reason', label: '事由', minWidth: 140 },
          { prop: 'status', label: '状态', type: 'status' }],
        filters: [{ prop: 'leave_type', label: '假别', options: Object.entries(LEAVE_TYPES).map(([value, v]) => ({ value, label: v[0] })) },
          { prop: 'status', label: '状态', options: st('PENDING', 'APPROVED', 'REJECTED', 'WITHDRAWN') }]
      }) },
      { path: '/hr/piece-rates', title: '计件单价', roles: ['ADMIN', 'HR', 'MANAGEMENT', 'FINANCE', 'PRODUCTION'], ...list({
        key: 'piece-rates',
        toolbar: [{ label: '调整单价', roles: ['ADMIN', 'HR', 'PRODUCTION'], run: newPieceRate }],
        columns: [{ prop: 'line_code', label: '产线', width: 80 }, { prop: 'rate', label: '单价（元/台良品）', type: 'money' },
          { prop: 'effective_from', label: '生效日期', width: 110 }, { prop: 'remark', label: '说明', minWidth: 200 }]
      }) },
      { path: '/hr/payrolls', title: '工资表', roles: ['ADMIN', 'HR', 'MANAGEMENT', 'FINANCE'], ...list({
        key: 'payrolls', clickable: true, detail: markRaw(PayrollDetail), detailTitle: '工资表',
        toolbar: [{ label: '核算工资', roles: ['ADMIN', 'HR'], run: calcPayroll }],
        columns: [
          { prop: 'period', label: '期间', width: 90 }, { prop: 'status', label: '状态', type: 'status', map: PAYROLL_STATUS }, { prop: 'headcount', label: '人数', type: 'int' },
          { prop: 'gross_total', label: '应发', type: 'money' }, { prop: 'net_total', label: '实发', type: 'money' }, { prop: 'tax_total', label: '个税', type: 'money' },
          { prop: 'social_employee', label: '个人社保公积金', type: 'money' }, { prop: 'social_employer', label: '单位社保公积金', type: 'money' },
          { prop: 'employer_cost', label: '企业人工成本', type: 'money' }, { prop: 'calc_date', label: '核算日', width: 100 }, { prop: 'paid_date', label: '发放日', width: 100 }]
      }) }
    ]
  },
  {
    title: '系统', icon: 'Setting',
    items: [
      { path: '/system/audit', title: '审计日志', roles: ['ADMIN'], ...list({
        key: 'audit-logs',
        columns: [
          { prop: 'created_at', label: '时间', width: 160 }, { prop: 'username', label: '用户' }, { prop: 'action', label: '操作', minWidth: 160 },
          { prop: 'success', label: '成功', type: 'bool', width: 70 }, { prop: 'error_msg', label: '失败原因', minWidth: 200 }, { prop: 'detail', label: '参数', minWidth: 300 }]
      }) }
    ]
  }
]
