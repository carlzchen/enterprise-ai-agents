<template>
  <div class="page">
    <div class="page-card org">
      <div class="left">
        <el-tree :data="tree" node-key="id" default-expand-all :expand-on-click-node="false" highlight-current
                 :props="{ label: 'name', children: 'children' }" @node-click="select">
          <template #default="{ data }">
            <span>{{ data.name }} <span class="muted">{{ data.total_headcount }} 人{{ data.manager_name ? ' · ' + data.manager_name : '' }}</span></span>
          </template>
        </el-tree>
      </div>
      <div class="right">
        <div class="toolbar">
          <b>{{ current?.name || '全公司' }}</b>
          <el-input v-model="kw" placeholder="姓名 / 工号" clearable style="width: 180px" @change="load" />
          <el-select v-model="status" clearable placeholder="状态" style="width: 110px" @change="load">
            <el-option label="在职" value="ACTIVE" /><el-option label="试用" value="PROBATION" /><el-option label="离职" value="LEFT" />
          </el-select>
          <el-button v-if="hasRole(['ADMIN','HR'])" type="primary" @click="hire">办理入职</el-button>
        </div>
        <el-table :data="rows" size="small" border @row-click="open" style="cursor: pointer">
          <el-table-column prop="emp_no" label="工号" width="90" />
          <el-table-column prop="name" label="姓名" width="90" />
          <el-table-column prop="dept_name" label="部门" width="120" />
          <el-table-column prop="position_name" label="岗位" width="100" />
          <el-table-column label="产线班组" width="90"><template #default="{ row }">{{ row.line_code ? row.line_code + '-' + row.team : '' }}</template></el-table-column>
          <el-table-column prop="manager_name" label="直属上级" width="90" />
          <el-table-column label="计薪" width="60"><template #default="{ row }">{{ row.wage_type === 'PIECE' ? '计件' : '月薪' }}</template></el-table-column>
          <el-table-column prop="hire_date" label="入职" width="100" />
          <el-table-column label="状态" width="70"><template #default="{ row }"><el-tag size="small" :type="STATUS[row.status]?.[1]">{{ STATUS[row.status]?.[0] }}</el-tag></template></el-table-column>
          <el-table-column prop="username" label="账号" width="80" />
        </el-table>
        <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" @current-change="load" style="margin-top: 10px" />
      </div>
    </div>
    <el-drawer v-model="drawer" title="员工档案" size="60%" destroy-on-close>
      <EmployeeDetail v-if="drawer" :id="currentId" @changed="refresh" />
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { openForm } from '../api/shared'
import { STATUS, today } from '../api/format'
import EmployeeDetail from './EmployeeDetail.vue'

const tree = ref([])
const current = ref(null)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const kw = ref('')
const status = ref('')
const drawer = ref(false)
const currentId = ref(null)

async function load() {
  // 选中部门时包含其全部下级部门
  const params = { page: page.value, size: 20, kw: kw.value || undefined, status: status.value || undefined, deptId: current.value?.id }
  const r = await http.get('/org/employees', { params })
  rows.value = r.records
  total.value = r.total
}
function select(node) { current.value = node; page.value = 1; load() }
function open(row) { currentId.value = row.id; drawer.value = true }
async function refresh() { tree.value = await http.get('/org/tree'); load() }

async function hire() {
  const depts = []
  const walk = (ns, prefix = '') => ns.forEach(n => { depts.push({ value: n.id, label: prefix + n.name }); walk(n.children || [], prefix + '　') })
  walk(tree.value)
  openForm({
    title: '办理入职',
    fields: [
      { prop: 'name', label: '姓名', required: true },
      { prop: 'gender', label: '性别', type: 'select', options: [{ value: 'M', label: '男' }, { value: 'F', label: '女' }] },
      { prop: 'deptId', label: '部门', type: 'select', required: true, options: depts },
      { prop: 'positionId', label: '岗位', type: 'select', required: true, options: positions.value.map(p => ({ value: p.id, label: `${p.name}（${p.grade}）` })) },
      { prop: 'employmentType', label: '用工类型', type: 'select', options: [{ value: 'FULL_TIME', label: '正式' }, { value: 'DISPATCH', label: '劳务派遣' }, { value: 'INTERN', label: '实习' }] },
      { prop: 'wageType', label: '计薪方式', type: 'select', options: [{ value: 'MONTHLY', label: '月薪' }, { value: 'PIECE', label: '计件' }] },
      { prop: 'lineCode', label: '产线（计件员工）' }, { prop: 'team', label: '班组（A/B/C）' },
      { prop: 'baseSalary', label: '月薪 / 保底工资', type: 'number', precision: 2, required: true },
      { prop: 'hireDate', label: '入职日期', type: 'date', required: true }],
    initial: { gender: 'M', employmentType: 'FULL_TIME', wageType: 'MONTHLY', hireDate: today(), deptId: current.value?.id },
    onSubmit: async m => { await http.post('/org/employees', m); ElMessage.success('入职已办理，试用期 3 个月'); refresh() }
  })
}
const positions = ref([])

onMounted(async () => { positions.value = await http.get('/org/positions'); refresh() })
</script>

<style scoped>
.org { display: flex; gap: 16px; }
.left { width: 300px; border-right: 1px solid #eee; padding-right: 8px; max-height: 78vh; overflow: auto; }
.right { flex: 1; min-width: 0; }
</style>
