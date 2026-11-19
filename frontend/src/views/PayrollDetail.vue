<template>
  <div v-if="p">
    <el-descriptions :column="4" border size="small">
      <el-descriptions-item label="期间">{{ p.period }}</el-descriptions-item>
      <el-descriptions-item label="状态"><el-tag size="small" :type="PR[p.status]?.[1]">{{ PR[p.status]?.[0] }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="人数">{{ p.headcount }}</el-descriptions-item>
      <el-descriptions-item label="发放日">{{ p.paid_date || '-' }}</el-descriptions-item>
      <el-descriptions-item label="应发合计">{{ money(p.gross_total) }}</el-descriptions-item>
      <el-descriptions-item label="实发合计">{{ money(p.net_total) }}</el-descriptions-item>
      <el-descriptions-item label="代扣个税">{{ money(p.tax_total) }}</el-descriptions-item>
      <el-descriptions-item label="企业人工成本">{{ money(p.employer_cost) }}</el-descriptions-item>
    </el-descriptions>
    <div class="actions">
      <el-button v-if="p.status === 'DRAFT' && hasRole(['ADMIN','HR'])" @click="recalc">重新核算</el-button>
      <el-button v-if="p.status === 'DRAFT' && hasRole(['ADMIN','HR'])" type="primary" @click="submit">提交发放审批</el-button>
      <el-button v-if="p.status === 'APPROVED' && hasRole(['ADMIN','FINANCE'])" type="success" @click="pay">发放工资</el-button>
      <span class="muted">计提：制造费用 / 销售费用 / 管理费用 / 研发费用 ← 应付职工薪酬；发放：应付职工薪酬 → 银行存款 + 应交个税</span>
    </div>
    <div class="filters">
      <el-input v-model="kw" placeholder="姓名 / 工号" clearable style="width: 160px" />
      <el-select v-model="wage" clearable placeholder="计薪方式" style="width: 110px"><el-option label="月薪" value="MONTHLY" /><el-option label="计件" value="PIECE" /></el-select>
    </div>
    <el-table :data="filtered" size="small" border max-height="520">
      <el-table-column prop="emp_no" label="工号" width="84" fixed />
      <el-table-column prop="name" label="姓名" width="76" fixed />
      <el-table-column prop="dept_name" label="部门" width="110" />
      <el-table-column label="班组" width="70"><template #default="{ row }">{{ row.line_code ? row.line_code + '-' + row.team : '' }}</template></el-table-column>
      <el-table-column label="出勤/应出勤" width="90" align="right"><template #default="{ row }">{{ Number(row.attend_days) }}/{{ row.scheduled_days }}</template></el-table-column>
      <el-table-column label="请假/旷工" width="80" align="right"><template #default="{ row }">{{ Number(row.leave_days) }}/{{ Number(row.absent_days) }}</template></el-table-column>
      <el-table-column label="加班h" width="64" align="right"><template #default="{ row }">{{ Number(row.overtime_hours) }}</template></el-table-column>
      <el-table-column v-for="c in MONEY" :key="c[0]" :label="c[1]" :width="c[2] || 90" align="right">
        <template #default="{ row }">{{ Number(row[c[0]]) ? money(row[c[0]]) : '' }}</template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { money, today } from '../api/format'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const p = ref(null)
const slips = ref([])
const kw = ref('')
const wage = ref('')
const PR = { DRAFT: ['草稿', 'info'], PENDING: ['审批中', 'warning'], APPROVED: ['已审批入账', ''], PAID: ['已发放', 'success'] }
const MONEY = [['base_pay', '基本工资'], ['piece_pay', '计件'], ['guarantee_topup', '保底补差'], ['overtime_pay', '加班费'], ['deduction', '缺勤扣款'],
  ['gross_pay', '应发', 100], ['social_employee', '个人社保公积金', 110], ['income_tax', '个税'], ['net_pay', '实发', 100], ['social_employer', '单位社保公积金', 110]]
const filtered = computed(() => slips.value.filter(s => (!kw.value || s.name.includes(kw.value) || s.emp_no.includes(kw.value)) && (!wage.value || s.wage_type === wage.value)))

async function load() {
  const r = await http.get('/views/payrolls', { params: { size: 100 } })
  p.value = r.records.find(x => x.id === props.id)
  slips.value = (await http.get('/views/payslips', { params: { payroll_id: props.id, size: 100, page: 1 } })).records
  // 工资条较多时分页取全
  let page = 2
  while (slips.value.length < p.value.slip_count) {
    const more = (await http.get('/views/payslips', { params: { payroll_id: props.id, size: 100, page: page++ } })).records
    if (!more.length) break
    slips.value.push(...more)
  }
}
const done = async msg => { ElMessage.success(msg); await load(); emit('changed') }
async function recalc() { await http.post('/hr/payrolls/calculate', { period: p.value.period }); emit('changed'); ElMessage.success('已重算，请重新打开') }
async function submit() { await http.post(`/hr/payrolls/${props.id}/submit`); await done('已提交：人力负责人 → 财务负责人 → 总经理') }
async function pay() {
  await ElMessageBox.confirm(`发放 ${p.value.period} 工资：实发 ${money(p.value.net_total)}，同时缴纳社保公积金与个税`, '发放工资', { type: 'warning' })
  await http.post(`/hr/payrolls/${props.id}/pay`, { date: today() }); await done('已发放')
}
onMounted(load)
</script>

<style scoped>
.actions { margin: 12px 0; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.filters { display: flex; gap: 8px; margin-bottom: 8px; }
</style>
