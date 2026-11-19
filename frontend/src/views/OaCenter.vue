<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-dropdown @command="start">
          <el-button type="primary">发起审批</el-button>
          <template #dropdown><el-dropdown-menu>
            <el-dropdown-item v-for="d in startable" :key="d.code" :command="d">{{ d.name }}</el-dropdown-item>
          </el-dropdown-menu></template>
        </el-dropdown>
        <span v-if="!auth.user?.employeeId" class="muted">当前账号未关联员工档案，无法发起或办理审批</span>
      </div>
      <el-tabs v-model="box" @tab-change="load">
        <el-tab-pane :label="`待办${todoCount ? '（' + todoCount + '）' : ''}`" name="todo" />
        <el-tab-pane label="已办" name="done" />
        <el-tab-pane label="我发起的" name="started" />
      </el-tabs>
      <el-table :data="rows" size="small" border @row-click="open" style="cursor: pointer">
        <el-table-column prop="instance_no" label="流程号" width="140" />
        <el-table-column prop="title" label="标题" min-width="280" />
        <el-table-column v-if="box !== 'started'" prop="applicant_name" label="申请人" width="90" />
        <el-table-column v-if="box !== 'started'" prop="node_name" label="节点" width="120" />
        <el-table-column v-if="box === 'started'" prop="def_name" label="类型" width="100" />
        <el-table-column v-if="box === 'started'" prop="pending_with" label="当前审批人" width="130" />
        <el-table-column label="状态" width="100"><template #default="{ row }">
          <el-tag size="small" :type="STATUS[st(row)]?.[1]">{{ STATUS[st(row)]?.[0] || st(row) }}</el-tag>
        </template></el-table-column>
        <el-table-column label="时间" width="150"><template #default="{ row }">{{ fmt(row.acted_at || row.created_at || row.started_at) }}</template></el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="total, prev, pager, next" @current-change="load" style="margin-top: 10px" />
    </div>
    <el-drawer v-model="drawer" title="审批详情" size="60%" destroy-on-close>
      <WfDetail v-if="drawer" :id="currentId" @changed="load" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api/http'
import { auth } from '../api/auth'
import { openForm } from '../api/shared'
import { STATUS, today } from '../api/format'
import WfDetail from './WfDetail.vue'

const box = ref('todo')
const rows = ref([])
const total = ref(0)
const page = ref(1)
const todoCount = ref(0)
const defs = ref([])
const drawer = ref(false)
const currentId = ref(null)
const startable = computed(() => defs.value.filter(d => d.code !== 'PAYROLL'))
const fmt = v => v ? String(v).replace('T', ' ').slice(0, 16) : ''
const st = r => box.value === 'started' ? r.status : box.value === 'todo' ? 'PENDING' : r.status

async function load() {
  if (!auth.user?.employeeId) return
  const r = await http.get(`/oa/my/${box.value}`, { params: { page: page.value, size: 20 } })
  rows.value = r.records
  total.value = r.total
  if (box.value === 'todo') todoCount.value = r.total
}

function open(row) { currentId.value = row.instance_id || row.id; drawer.value = true }

const EXPENSE_FIELDS = [
  { prop: 'category', label: '费用类别', type: 'select', required: true, options: ['差旅费', '业务招待费', '办公费', '运输费', '维修费', '培训费'].map(v => ({ value: v, label: v })) },
  { prop: 'amount', label: '金额（元）', type: 'number', precision: 2, required: true },
  { prop: 'description', label: '说明', required: true }]

function start(def) {
  const schema = def.code === 'EXPENSE' ? EXPENSE_FIELDS : JSON.parse(def.form_schema || '[]').map(f => ({
    ...f, type: f.type === 'textarea' ? undefined : f.type,
    options: f.options?.map(([value, label]) => ({ value, label })), precision: f.type === 'number' ? 2 : undefined }))
  openForm({
    title: `发起：${def.name}`,
    fields: schema,
    initial: Object.fromEntries(schema.filter(f => f.type === 'date').map(f => [f.prop, today()])),
    onSubmit: async form => {
      const route = await http.post('/oa/preview', { defCode: def.code, form })
      const r = await http.post('/oa/submit', { defCode: def.code, form })
      ElMessage.success(`已提交，审批路线：${route.filter(n => !n.note).map(n => n.name).join(' → ') || '无需审批，自动通过'}`)
      box.value = 'started'
      currentId.value = r.id
      drawer.value = true
      load()
    }
  })
}

onMounted(async () => {
  defs.value = await http.get('/oa/definitions')
  await load()
})
</script>
