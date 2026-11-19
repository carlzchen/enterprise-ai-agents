<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-select v-model="projectId" style="width: 260px" @change="load">
          <el-option v-for="p in projects" :key="p.id" :label="`${p.project_key} ${p.name}${p.status === 'ARCHIVED' ? '（已归档）' : ''}`" :value="p.id" />
        </el-select>
        <template v-if="b">
          <el-tag>{{ b.board_type === 'SCRUM' ? '迭代' : '看板' }}</el-tag>
          <span class="muted">负责人 {{ b.lead_name }}{{ b.plm_project_no ? ' · NPI ' + b.plm_project_no + '（' + b.plm_stage + '）' : '' }} · 未完成 {{ b.open_issues }} · 缺陷 {{ b.open_bugs }} · 工时 {{ Number(b.logged_hours) }}h</span>
          <span v-if="b.sprint" class="muted">｜{{ b.sprint.name }}：{{ b.sprint.start_date }} ~ {{ b.sprint.end_date }}，承诺 {{ b.sprint.committed_points }} 点，已完成 {{ b.sprint.done_points }} 点</span>
          <el-button v-if="canEdit" type="primary" size="small" @click="newIssue">新建问题</el-button>
        </template>
      </div>
      <el-tabs v-if="b" v-model="tab" @tab-change="tab === 'metrics' && loadMetrics()">
        <el-tab-pane label="看板" name="board">
          <div class="cols">
            <div v-for="c in COLS" :key="c[0]" class="col" @dragover.prevent @drop="drop(c[0])">
              <div class="col-head">{{ c[1] }} <span class="muted">{{ byStatus(c[0]).length }}</span></div>
              <div v-for="i in byStatus(c[0])" :key="i.id" class="card" draggable="true" @dragstart="dragging = i" @click="open(i.id)">
                <div class="k"><el-tag size="small" :type="TYPE[i.type][1]">{{ TYPE[i.type][0] }}</el-tag> {{ i.issue_key }}
                  <span class="pri" :class="i.priority">{{ PRI[i.priority] }}</span></div>
                <div class="t">{{ i.title }}</div>
                <div class="m">{{ i.assignee_name || '未分配' }}<span v-if="i.story_points"> · {{ i.story_points }} 点</span><span v-if="i.complaint_no"> · {{ i.complaint_no }}</span></div>
              </div>
            </div>
          </div>
        </el-tab-pane>
        <el-tab-pane :label="`待办池（${b.backlog.length}）`" name="backlog">
          <el-table :data="b.backlog" size="small" border @row-click="r => open(r.id)" style="cursor: pointer">
            <el-table-column prop="issue_key" label="编号" width="90" /><el-table-column label="类型" width="70"><template #default="{ row }">{{ TYPE[row.type][0] }}</template></el-table-column>
            <el-table-column prop="title" label="标题" min-width="240" /><el-table-column prop="story_points" label="点" width="50" />
            <el-table-column label="优先级" width="70"><template #default="{ row }">{{ PRI[row.priority] }}</template></el-table-column>
            <el-table-column v-if="b.sprint && canEdit" label="" width="110"><template #default="{ row }">
              <el-button link type="primary" size="small" @click.stop="toSprint(row)">移入当前迭代</el-button></template></el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="史诗" name="epics">
          <div v-for="e in b.epics" :key="e.id" class="epic" @click="open(e.id)">
            <b>{{ e.issue_key }} {{ e.title }}</b> <span class="muted">{{ e.done || 0 }}/{{ e.total }}</span>
            <el-progress :percentage="e.total ? Math.round((e.done || 0) * 100 / e.total) : 0" :stroke-width="10" />
          </div>
        </el-tab-pane>
        <el-tab-pane label="度量" name="metrics">
          <div class="toolbar">
            <el-select v-if="b.sprints.length" v-model="sprintId" placeholder="选择迭代看燃尽图" style="width: 240px" @change="loadBurndown">
              <el-option v-for="s in b.sprints" :key="s.id" :label="`${s.name}（${s.start_date}，${s.status}）`" :value="s.id" />
            </el-select>
            <span v-if="m" class="muted">速率（近 5 个迭代）{{ m.velocity.average }} ± {{ m.velocity.stdDev }} 点；周期时间 p50 {{ fix(m.cycleTime.p50) }} 天 · p85 {{ fix(m.cycleTime.p85) }} 天 · p95 {{ fix(m.cycleTime.p95) }} 天</span>
          </div>
          <div class="grid">
            <div ref="burnEl" class="chart" /><div ref="velEl" class="chart" />
            <div ref="cfdEl" class="chart" /><div ref="bugEl" class="chart" />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
    <el-drawer v-model="drawer" title="问题详情" size="55%" destroy-on-close>
      <IssueDetail v-if="drawer" :id="currentId" @changed="load" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { openForm } from '../api/shared'
import IssueDetail from './IssueDetail.vue'

const TYPE = { EPIC: ['史诗', 'warning'], STORY: ['需求', 'success'], TASK: ['任务', ''], BUG: ['缺陷', 'danger'] }
const PRI = { HIGHEST: '最高', HIGH: '高', MEDIUM: '中', LOW: '低' }
const COLS = [['TODO', '待办'], ['IN_PROGRESS', '进行中'], ['IN_REVIEW', '评审中'], ['DONE', '完成']]
const projects = ref([])
const projectId = ref(null)
const b = ref(null)
const m = ref(null)
const tab = ref('board')
const sprintId = ref(null)
const drawer = ref(false)
const currentId = ref(null)
const dragging = ref(null)
const burnEl = ref(), velEl = ref(), cfdEl = ref(), bugEl = ref()
let charts = {}
const canEdit = computed(() => hasRole(['ADMIN', 'ENGINEER', 'QUALITY', 'PRODUCTION', 'MANAGEMENT']))
const byStatus = s => (b.value?.issues || []).filter(i => i.status === s)
const fix = v => v == null ? '-' : Number(v).toFixed(1)

async function load() {
  b.value = await http.get(`/pm/projects/${projectId.value}/board`)
  sprintId.value = b.value.sprint?.id || b.value.sprints[0]?.id
  if (tab.value === 'metrics') loadMetrics()
}
function open(id) { currentId.value = id; drawer.value = true }

async function drop(to) {
  const i = dragging.value
  dragging.value = null
  if (!i || i.status === to || !canEdit.value) return
  let resolution = null
  if (to === 'DONE') resolution = i.status === 'TODO' ? 'WONT_FIX' : 'FIXED'
  try {
    await http.post(`/pm/issues/${i.id}/transition`, { to, resolution })
    ElMessage.success(`${i.issue_key} → ${COLS.find(c => c[0] === to)[1]}`)
  } finally { load() }
}
async function toSprint(row) { await http.post(`/pm/issues/${row.id}/sprint`, { sprintId: b.value.sprint.id }); load() }

function newIssue() {
  openForm({
    title: `新建问题 - ${b.value.project_key}`,
    fields: [
      { prop: 'type', label: '类型', type: 'select', required: true, options: Object.entries(TYPE).map(([value, v]) => ({ value, label: v[0] })) },
      { prop: 'title', label: '标题', required: true }, { prop: 'description', label: '描述' },
      { prop: 'priority', label: '优先级', type: 'select', options: Object.entries(PRI).map(([value, label]) => ({ value, label })) },
      { prop: 'storyPoints', label: '故事点', type: 'number', precision: 0 },
      { prop: 'epicId', label: '所属史诗', type: 'select', options: b.value.epics.map(e => ({ value: e.id, label: `${e.issue_key} ${e.title}` })) }],
    initial: { type: 'STORY', priority: 'MEDIUM', storyPoints: 3 },
    onSubmit: async f => { const r = await http.post('/pm/issues', { ...f, projectId: projectId.value }); ElMessage.success(`已创建 ${r.issue_key}`); load() }
  })
}

async function loadMetrics() {
  m.value = await http.get(`/pm/projects/${projectId.value}/metrics`, { params: { to: '2026-09-28', from: '2025-10-01' } })
  await nextTick()
  Object.values(charts).forEach(c => c.dispose())
  charts = { burn: echarts.init(burnEl.value), vel: echarts.init(velEl.value), cfd: echarts.init(cfdEl.value), bug: echarts.init(bugEl.value) }
  const sp = m.value.sprints.slice(-10)
  charts.vel.setOption({ title: { text: '速率（承诺 vs 完成）', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 40, right: 20, bottom: 30 }, xAxis: { data: sp.map(s => s.name) }, yAxis: {},
    series: [{ name: '承诺', type: 'bar', data: sp.map(s => s.committed_points) }, { name: '完成', type: 'bar', data: sp.map(s => s.completed_points) }] })
  const f = m.value.cumulativeFlow
  charts.cfd.setOption({ title: { text: '累积流图', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 40, right: 20, bottom: 30 }, xAxis: { data: f.map(x => String(x.date).slice(2)) }, yAxis: {},
    series: [['DONE', '完成'], ['IN_REVIEW', '评审中'], ['IN_PROGRESS', '进行中'], ['TODO', '待办']].map(([k, n]) => ({ name: n, type: 'line', stack: 'f', areaStyle: {}, showSymbol: false, data: f.map(x => x[k]) })) })
  const bt = m.value.bugTrend
  charts.bug.setOption({ title: { text: '缺陷新增与解决', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 40, right: 20, bottom: 30 }, xAxis: { data: bt.map(x => x.month) }, yAxis: {},
    series: [{ name: '新增', type: 'bar', data: bt.map(x => x.created) }, { name: '已解决', type: 'bar', data: bt.map(x => x.resolved) }] })
  loadBurndown()
}
async function loadBurndown() {
  if (!sprintId.value || !charts.burn) return
  const r = await http.get(`/pm/sprints/${sprintId.value}/burndown`)
  charts.burn.setOption({ title: { text: `燃尽图 · ${r.name}`, textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 40, right: 20, bottom: 30 }, xAxis: { data: r.points.map(p => String(p.date).slice(5)) }, yAxis: {},
    series: [{ name: '剩余', type: 'line', data: r.points.map(p => p.remaining) }, { name: '理想', type: 'line', lineStyle: { type: 'dashed' }, data: r.points.map(p => p.ideal) }] }, true)
}
const resize = () => Object.values(charts).forEach(c => c.resize())
onMounted(async () => {
  projects.value = (await http.get('/views/pm-projects', { params: { size: 50, sort: 'id', order: 'asc' } })).records
  projectId.value = (projects.value.find(p => p.project_key === 'JM') || projects.value[0])?.id
  if (projectId.value) await load()
  window.addEventListener('resize', resize)
})
onBeforeUnmount(() => { window.removeEventListener('resize', resize); Object.values(charts).forEach(c => c.dispose()) })
</script>

<style scoped>
.cols { display: grid; grid-template-columns: repeat(4, minmax(200px, 1fr)); gap: 10px; }
.col { background: #f3f5f9; border-radius: 6px; padding: 8px; min-height: 300px; }
.col-head { font-weight: 600; margin-bottom: 8px; }
.card { background: #fff; border-radius: 4px; padding: 8px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,.08); cursor: grab; }
.card .t { margin: 4px 0; line-height: 1.4; }
.card .m { font-size: 12px; color: #7a8599; }
.pri { float: right; font-size: 12px; }
.pri.HIGHEST, .pri.HIGH { color: #c0392b; }
.epic { padding: 8px 0; cursor: pointer; border-bottom: 1px dashed #e5e8ef; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chart { height: 280px; }
@media (max-width: 900px) { .cols { grid-template-columns: repeat(2, 1fr); } .grid { grid-template-columns: 1fr; } }
</style>
