<template>
  <div v-if="e">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="工号">{{ e.emp_no }}</el-descriptions-item>
      <el-descriptions-item label="姓名">{{ e.name }}（{{ e.gender === 'F' ? '女' : '男' }}）</el-descriptions-item>
      <el-descriptions-item label="状态"><el-tag size="small" :type="STATUS[e.status]?.[1]">{{ STATUS[e.status]?.[0] }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="部门">{{ e.dept_name }}</el-descriptions-item>
      <el-descriptions-item label="岗位">{{ e.position_name }}（{{ e.grade }}）</el-descriptions-item>
      <el-descriptions-item label="直属上级">{{ e.manager_name || '-' }}</el-descriptions-item>
      <el-descriptions-item label="产线 / 班组">{{ e.line_code ? `${e.line_code} 班组${e.team}` : '-' }}</el-descriptions-item>
      <el-descriptions-item label="用工 / 计薪">{{ EMP_TYPES[e.employment_type] }} / {{ e.wage_type === 'PIECE' ? '计件' : '月薪' }}</el-descriptions-item>
      <el-descriptions-item label="工资">{{ e.base_salary == null ? '（无权限查看）' : money(e.base_salary) + (e.wage_type === 'PIECE' ? '（保底）' : '') }}</el-descriptions-item>
      <el-descriptions-item label="入职">{{ e.hire_date }}（司龄 {{ e.tenure_months }} 个月）</el-descriptions-item>
      <el-descriptions-item label="离职">{{ e.leave_date || '-' }} {{ e.leave_reason || '' }}</el-descriptions-item>
      <el-descriptions-item label="系统账号">{{ e.username || '未开通' }}</el-descriptions-item>
      <el-descriptions-item label="汇报链" :span="3">{{ (e.reportingChain || []).join(' → ') || '-' }}</el-descriptions-item>
    </el-descriptions>
    <div v-if="hasRole(['ADMIN','HR']) && e.status !== 'LEFT'" class="actions">
      <el-button @click="salary">调薪</el-button>
      <el-button v-if="!e.username" @click="account">开通账号</el-button>
      <el-button type="danger" plain @click="leave">办理离职</el-button>
    </div>
    <h4>异动记录</h4>
    <el-table :data="e.changes" size="small" border>
      <el-table-column prop="change_date" label="日期" width="100" />
      <el-table-column label="类型" width="80"><template #default="{ row }">{{ CHANGE[row.change_type] || row.change_type }}</template></el-table-column>
      <el-table-column label="部门" min-width="160"><template #default="{ row }">{{ [row.from_dept, row.to_dept].filter(Boolean).join(' → ') }}</template></el-table-column>
      <el-table-column label="工资" min-width="140"><template #default="{ row }">{{ row.to_salary ? `${money(row.from_salary)} → ${money(row.to_salary)}` : '' }}</template></el-table-column>
      <el-table-column prop="remark" label="说明" min-width="180" />
    </el-table>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { openForm } from '../api/shared'
import { STATUS, money, today } from '../api/format'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const e = ref(null)
const EMP_TYPES = { FULL_TIME: '正式', DISPATCH: '劳务派遣', INTERN: '实习' }
const CHANGE = { HIRE: '入职', REGULAR: '转正', TRANSFER: '调岗', SALARY: '调薪', LEAVE: '离职' }
async function load() { e.value = await http.get(`/org/employees/${props.id}`) }
const done = async msg => { ElMessage.success(msg); await load(); emit('changed') }

function salary() {
  openForm({ title: `调薪 - ${e.value.name}`, fields: [{ prop: 'baseSalary', label: '新工资', type: 'number', precision: 2, required: true },
    { prop: 'date', label: '生效日期', type: 'date', required: true }, { prop: 'remark', label: '说明' }],
    initial: { baseSalary: Number(e.value.base_salary), date: today() },
    onSubmit: async m => { await http.post(`/org/employees/${props.id}/salary`, m); await done('已调薪') } })
}
function account() {
  openForm({ title: `开通账号 - ${e.value.name}`, fields: [{ prop: 'username', label: '登录名', required: true },
    { prop: 'role', label: '角色', type: 'select', required: true, options: ['SALES', 'PURCHASE', 'WAREHOUSE', 'PRODUCTION', 'QUALITY', 'FINANCE', 'ENGINEER', 'HR', 'MANAGEMENT'].map(v => ({ value: v, label: v })) },
    { prop: 'initialPassword', label: '初始密码（≥8 位）', required: true }],
    onSubmit: async m => { await http.post(`/org/employees/${props.id}/account`, m); await done('账号已开通，离职时自动停用') } })
}
async function leave() {
  const { value } = await ElMessageBox.prompt('离职原因', `办理离职 - ${e.value.name}（当天生效并停用账号）`, { inputValue: '个人原因' })
  await http.post(`/org/employees/${props.id}/leave`, { date: today(), reason: value })
  await done('离职已生效')
}
onMounted(load)
</script>

<style scoped>
.actions { margin: 12px 0; display: flex; gap: 8px; }
h4 { margin: 16px 0 8px; }
</style>
