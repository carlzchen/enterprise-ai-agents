<template>
  <div v-if="d">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="流程号">{{ d.instance_no }}</el-descriptions-item>
      <el-descriptions-item label="类型">{{ d.def_name }}</el-descriptions-item>
      <el-descriptions-item label="状态"><el-tag size="small" :type="STATUS[d.status]?.[1]">{{ STATUS[d.status]?.[0] || d.status }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="申请人">{{ d.applicant_name }}（{{ d.applicant_dept }}）</el-descriptions-item>
      <el-descriptions-item label="发起时间">{{ fmt(d.started_at) }}</el-descriptions-item>
      <el-descriptions-item label="结束时间">{{ fmt(d.finished_at) || '-' }}</el-descriptions-item>
      <el-descriptions-item label="标题" :span="3">{{ d.title }}</el-descriptions-item>
      <el-descriptions-item v-for="(v, k) in d.form || {}" :key="k" :label="LABELS[k] || k">{{ v }}</el-descriptions-item>
    </el-descriptions>

    <div v-if="myTask" class="actions">
      <el-input v-model="comment" placeholder="审批意见（驳回必填）" style="width: 320px" />
      <el-button type="primary" @click="act('approve')">同意</el-button>
      <el-button type="danger" @click="act('reject')">驳回</el-button>
      <el-button @click="pick('transfer')">转交</el-button>
      <el-button @click="pick('add-sign')">加签</el-button>
    </div>
    <div v-else-if="canWithdraw" class="actions">
      <el-button @click="withdraw">撤回</el-button>
      <span class="muted">还没有审批人同意，可以撤回</span>
    </div>

    <h4>审批路线</h4>
    <el-steps :active="activeStep" finish-status="success" align-center style="margin-bottom: 12px">
      <el-step v-for="n in d.route" :key="n.index" :title="n.name"
               :description="n.note || ((n.mode === 'AND' ? '会签：' : n.approvers.length > 1 ? '或签：' : '') + n.approvers.join('、'))" />
    </el-steps>

    <h4>审批记录</h4>
    <el-timeline>
      <el-timeline-item :timestamp="fmt(d.started_at)" type="primary">{{ d.applicant_name }} 发起申请</el-timeline-item>
      <el-timeline-item v-for="t in d.tasks" :key="t.id" :timestamp="fmt(t.acted_at || t.created_at)" :type="TASK[t.status]?.[1]">
        <b>{{ t.node_name }}</b> · {{ t.assignee_name }} · <el-tag size="small" :type="TASK[t.status]?.[1]">{{ TASK[t.status]?.[0] || t.status }}</el-tag>
        <span v-if="t.comment" class="muted">　{{ t.comment }}</span>
      </el-timeline-item>
    </el-timeline>

    <el-dialog v-model="picking" :title="pickMode === 'transfer' ? '转交给' : '加签给'" width="420px" append-to-body>
      <el-select v-model="target" filterable remote :remote-method="search" placeholder="输入姓名或工号" style="width: 100%">
        <el-option v-for="p in people" :key="p.id" :label="`${p.name}（${p.dept_name} ${p.position_name}）`" :value="p.id" />
      </el-select>
      <template #footer><el-button type="primary" :disabled="!target" @click="doPick">确定</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { auth } from '../api/auth'
import { STATUS } from '../api/format'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const d = ref(null)
const comment = ref('')
const picking = ref(false)
const pickMode = ref('')
const target = ref(null)
const people = ref([])

const TASK = { PENDING: ['待审批', 'warning'], APPROVED: ['同意', 'success'], REJECTED: ['驳回', 'danger'], TRANSFERRED: ['已转交', 'info'],
  CANCELLED: ['已取消', 'info'], SKIPPED: ['自动跳过', 'info'] }
const LABELS = { leaveType: '假别', startDate: '开始', endDate: '结束', reason: '事由', lastDay: '最后工作日', destination: '目的地', budget: '预算',
  sealType: '印章', document: '用印文件', copies: '份数', subject: '事项', detail: '说明', category: '费用类别', description: '说明', expenseNo: '报销单号', period: '工资期间' }

const me = computed(() => auth.user?.employeeId)
const myTask = computed(() => d.value?.tasks.find(t => t.status === 'PENDING' && t.assignee_emp_id === me.value))
const canWithdraw = computed(() => d.value?.status === 'RUNNING' && d.value.applicant_emp_id === me.value && !d.value.tasks.some(t => t.status === 'APPROVED'))
const activeStep = computed(() => d.value?.status === 'APPROVED' ? d.value.route.length : d.value?.current_node ?? 0)
const fmt = v => v ? String(v).replace('T', ' ').slice(0, 16) : ''

async function load() { d.value = await http.get(`/oa/instances/${props.id}`) }

async function act(kind) {
  await http.post(`/oa/tasks/${myTask.value.id}/${kind}`, { comment: comment.value })
  ElMessage.success(kind === 'approve' ? '已同意' : '已驳回')
  comment.value = ''
  await load(); emit('changed')
}

function pick(mode) { pickMode.value = mode; target.value = null; picking.value = true }
async function search(kw) { if (kw) people.value = await http.get('/oa/people', { params: { kw } }) }
async function doPick() {
  await http.post(`/oa/tasks/${myTask.value.id}/${pickMode.value}`, { toEmpId: target.value, comment: comment.value })
  ElMessage.success(pickMode.value === 'transfer' ? '已转交' : '已加签，加签人同意后本节点才通过')
  picking.value = false
  await load(); emit('changed')
}

async function withdraw() {
  await ElMessageBox.confirm('确认撤回该申请？', '撤回', { type: 'warning' })
  await http.post(`/oa/instances/${props.id}/withdraw`)
  ElMessage.success('已撤回')
  await load(); emit('changed')
}

onMounted(load)
</script>

<style scoped>
.actions { margin: 12px 0; display: flex; gap: 8px; align-items: center; }
h4 { margin: 16px 0 8px; }
</style>
