<template>
  <div v-if="d">
    <div class="head">
      <el-tag :type="TYPE[d.type][1]">{{ TYPE[d.type][0] }}</el-tag>
      <b>{{ d.issue_key }}</b> <span class="title">{{ d.title }}</span>
    </div>
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="状态"><el-tag size="small" :type="ST[d.status][1]">{{ ST[d.status][0] }}</el-tag> {{ d.resolution || '' }}</el-descriptions-item>
      <el-descriptions-item label="优先级">{{ PRI[d.priority] }}</el-descriptions-item>
      <el-descriptions-item label="故事点">{{ d.story_points ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="经办人">{{ d.assignee_name || '未分配' }}</el-descriptions-item>
      <el-descriptions-item label="报告人">{{ d.reporter_name }}</el-descriptions-item>
      <el-descriptions-item label="迭代">{{ d.sprint_name || '待办池' }}</el-descriptions-item>
      <el-descriptions-item label="史诗">{{ d.epic_key ? `${d.epic_key} ${d.epic_title}` : '-' }}</el-descriptions-item>
      <el-descriptions-item label="关联">{{ [d.ecn_no, d.complaint_no].filter(Boolean).join(' / ') || '-' }}</el-descriptions-item>
      <el-descriptions-item label="已登记工时">{{ Number(d.logged_hours) }} h</el-descriptions-item>
      <el-descriptions-item label="创建">{{ fmt(d.created_at) }}</el-descriptions-item>
      <el-descriptions-item label="开始 / 完成">{{ fmt(d.started_at) || '-' }} / {{ fmt(d.resolved_at) || '-' }}</el-descriptions-item>
      <el-descriptions-item label="周期">{{ d.cycle_days != null ? d.cycle_days + ' 天' : '-' }}</el-descriptions-item>
    </el-descriptions>
    <div v-if="d.description" class="desc">{{ d.description }}</div>
    <div class="actions" v-if="canEdit">
      <el-button v-for="t in transitions" :key="t.to" size="small" :type="t.type" @click="move(t)">{{ t.label }}</el-button>
      <el-button size="small" @click="logWork">登记工时</el-button>
    </div>
    <el-tabs>
      <el-tab-pane :label="`评论（${d.comments.length}）`">
        <div v-for="c in d.comments" :key="c.id" class="comment"><b>{{ c.emp_name }}</b> <span class="muted">{{ fmt(c.created_at) }}</span><div>{{ c.body }}</div></div>
        <div class="add"><el-input v-model="text" type="textarea" :rows="2" placeholder="添加评论" /><el-button size="small" @click="comment" :disabled="!text">发表</el-button></div>
      </el-tab-pane>
      <el-tab-pane :label="`工时（${d.worklogs.length}）`">
        <el-table :data="d.worklogs" size="small" border max-height="260">
          <el-table-column prop="work_date" label="日期" width="100" /><el-table-column prop="emp_name" label="人员" width="90" />
          <el-table-column prop="hours" label="小时" width="70" /><el-table-column prop="comment" label="说明" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="变更历史">
        <el-timeline>
          <el-timeline-item v-for="h in d.history" :key="h.id" :timestamp="fmt(h.changed_at)">
            {{ h.emp_name || '系统' }} · {{ FIELD[h.field] || h.field }}：{{ label(h.field, h.from_value) }} → {{ label(h.field, h.to_value) }}
          </el-timeline-item>
        </el-timeline>
      </el-tab-pane>
      <el-tab-pane v-if="d.children.length" :label="`子问题（${d.children.length}）`">
        <el-table :data="d.children" size="small" border>
          <el-table-column prop="issue_key" label="编号" width="90" /><el-table-column prop="title" label="标题" />
          <el-table-column label="状态" width="80"><template #default="{ row }">{{ ST[row.status][0] }}</template></el-table-column>
          <el-table-column prop="story_points" label="点" width="50" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane v-if="d.links.length" label="关联问题">
        <div v-for="(l, i) in d.links" :key="i">{{ l.link_type }} {{ l.dir === 'OUT' ? '→' : '←' }} {{ l.issue_key }} {{ l.title }}（{{ ST[l.status][0] }}）</div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { today } from '../api/format'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const d = ref(null)
const text = ref('')
const TYPE = { EPIC: ['史诗', 'warning'], STORY: ['需求', 'success'], TASK: ['任务', ''], BUG: ['缺陷', 'danger'] }
const ST = { TODO: ['待办', 'info'], IN_PROGRESS: ['进行中', ''], IN_REVIEW: ['评审中', 'warning'], DONE: ['完成', 'success'] }
const PRI = { HIGHEST: '最高', HIGH: '高', MEDIUM: '中', LOW: '低' }
const FIELD = { status: '状态', assignee: '经办人', sprint: '迭代' }
const canEdit = computed(() => hasRole(['ADMIN', 'ENGINEER', 'QUALITY', 'PRODUCTION', 'MANAGEMENT']))
const fmt = v => v ? String(v).replace('T', ' ').slice(0, 16) : ''
const label = (f, v) => v == null ? '空' : f === 'status' ? ST[v]?.[0] || v : v
const transitions = computed(() => {
  const s = d.value.status, t = d.value.type
  const out = []
  if (s === 'TODO') out.push({ to: 'IN_PROGRESS', label: '开始处理', type: 'primary' }, { to: 'DONE', label: '不修复关闭', res: 'WONT_FIX' })
  if (s === 'IN_PROGRESS') { out.push({ to: t === 'TASK' || t === 'EPIC' ? 'DONE' : 'IN_REVIEW', label: t === 'TASK' || t === 'EPIC' ? '完成' : '提交评审', type: 'primary', res: 'FIXED' }, { to: 'TODO', label: '退回待办' }) }
  if (s === 'IN_REVIEW') out.push({ to: 'DONE', label: '评审通过', type: 'success', res: 'FIXED' }, { to: 'IN_PROGRESS', label: '评审退回', type: 'danger' })
  if (s === 'DONE' && t !== 'TASK') out.push({ to: 'TODO', label: '重新打开' })
  return out
})
async function load() { d.value = await http.get(`/pm/issues/${props.id}`) }
async function move(t) {
  d.value = await http.post(`/pm/issues/${props.id}/transition`, { to: t.to, resolution: t.to === 'DONE' ? t.res : null })
  ElMessage.success(t.label); emit('changed')
}
async function comment() { d.value = await http.post(`/pm/issues/${props.id}/comments`, { body: text.value }); text.value = '' }
async function logWork() {
  const { value } = await ElMessageBox.prompt('今天在该问题上的工时（小时）', '登记工时', { inputValue: '2', inputPattern: /^\d+(\.\d)?$/ })
  d.value = await http.post(`/pm/issues/${props.id}/worklogs`, { date: today(), hours: Number(value) }); emit('changed')
}
onMounted(load)
</script>

<style scoped>
.head { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
.title { font-size: 16px; }
.desc { white-space: pre-wrap; background: #f6f8fb; padding: 10px; border-radius: 6px; margin: 10px 0; }
.actions { margin: 12px 0; display: flex; gap: 6px; flex-wrap: wrap; }
.comment { padding: 6px 0; border-bottom: 1px dashed #e5e8ef; }
.add { display: flex; gap: 8px; margin-top: 8px; align-items: flex-end; }
</style>
