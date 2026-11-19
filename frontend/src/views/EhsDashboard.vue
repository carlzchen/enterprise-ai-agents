<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" :clearable="false" @change="load" />
        <span class="muted">LTIFR = 损工事故 × 10⁶ ÷ 工时；TRIR = 可记录事故 × 2×10⁵ ÷ 工时（工时取考勤）；一般隐患 7 天、重大隐患 30 天整改；危废贮存不得超过一年</span>
      </div>
      <div v-if="d" class="kpis">
        <div class="kpi"><div class="v">{{ d.daysWithoutLti ?? '-' }}</div><div class="l">无损工事故天数（上次 {{ d.lastLostTime || '无' }}）</div></div>
        <div class="kpi"><div class="v">{{ d.safety.ltifr }}</div><div class="l">LTIFR（{{ d.safety.lostTime }} 起）</div></div>
        <div class="kpi"><div class="v">{{ d.safety.trir }}</div><div class="l">TRIR</div></div>
        <div class="kpi"><div class="v">{{ d.safety.severity }}</div><div class="l">严重率（损失 {{ d.safety.lostDays }} 天）</div></div>
        <div class="kpi"><div class="v">{{ Math.round(d.safety.hours / 1e4) }} 万</div><div class="l">总工时</div></div>
        <div class="kpi"><div class="v">{{ d.hazards.found }}（重大 {{ d.hazards.major }}）</div><div class="l">发现隐患</div></div>
        <div class="kpi"><div class="v" :style="{ color: d.hazards.on_time_rate < 0.9 ? '#c0392b' : '#3d8c5a' }">{{ pct(d.hazards.on_time_rate) }}</div><div class="l">按期整改率（平均 {{ d.hazards.avg_days }} 天）</div></div>
        <div class="kpi"><div class="v">{{ d.hazards.overdue_open }}</div><div class="l">逾期未整改</div></div>
      </div>
      <div class="grid">
        <div ref="trendEl" class="chart" />
        <div ref="pyrEl" class="chart" />
      </div>
      <h4>可记录 / 损工事故</h4>
      <el-table :data="d?.seriousIncidents || []" size="small" border>
        <el-table-column prop="incident_no" label="编号" width="140" /><el-table-column prop="occurred_date" label="日期" width="100" />
        <el-table-column prop="dept_name" label="部门" width="110" /><el-table-column label="类型" width="80"><template #default="{ row }">{{ TYPES[row.type] }}</template></el-table-column>
        <el-table-column prop="description" label="经过" min-width="220" /><el-table-column prop="injury" label="伤害" width="140" />
        <el-table-column prop="lost_days" label="损失工日" width="80" align="right" /><el-table-column prop="root_cause" label="根因" min-width="200" />
        <el-table-column prop="corrective_action" label="整改措施" min-width="240" />
      </el-table>
      <div class="grid">
        <div>
          <h4>隐患按部门</h4>
          <el-table :data="d?.hazardsByDept || []" size="small" border>
            <el-table-column prop="dept" label="部门" /><el-table-column prop="n" label="隐患" align="right" /><el-table-column prop="overdue" label="逾期" align="right" />
          </el-table>
          <h4>作业票</h4>
          <el-table :data="d?.permits || []" size="small" border>
            <el-table-column label="类型"><template #default="{ row }">{{ PERMITS[row.type] }}</template></el-table-column>
            <el-table-column prop="n" label="申请" align="right" /><el-table-column prop="closed" label="已关闭" align="right" /><el-table-column prop="rejected" label="驳回" align="right" />
          </el-table>
        </div>
        <div>
          <h4>隐患按类别</h4>
          <el-table :data="d?.hazardsByCategory || []" size="small" border>
            <el-table-column prop="category" label="类别" /><el-table-column prop="n" label="隐患" align="right" /><el-table-column prop="major" label="重大" align="right" />
          </el-table>
          <h4>危废贮存</h4>
          <el-table :data="d?.waste || []" size="small" border>
            <el-table-column prop="waste_code" label="类别" width="70" /><el-table-column prop="waste_name" label="名称" min-width="160" />
            <el-table-column prop="stockTon" label="在库(吨)" align="right" width="80" /><el-table-column prop="days" label="最早贮存(天)" align="right" width="100" />
            <el-table-column label="状态" width="70"><template #default="{ row }"><el-tag size="small" :type="row.status === 'OK' ? 'success' : row.status === 'WARNING' ? 'warning' : 'danger'">
              {{ { OK: '正常', WARNING: '预警', VIOLATION: '超期' }[row.status] }}</el-tag></template></el-table-column>
          </el-table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'
import { pct } from '../api/format'

const TYPES = { NEAR_MISS: '未遂', FIRST_AID: '急救', RECORDABLE: '可记录', LOST_TIME: '损工' }
const PERMITS = { HOT_WORK: '动火', HEIGHT: '高处', CONFINED: '受限空间', ELECTRICAL: '临时用电' }
const range = ref(['2024-10-01', '2026-09-28'])
const d = ref(null)
const trendEl = ref(), pyrEl = ref()
let charts = []
async function load() {
  d.value = await http.get('/ehs/dashboard', { params: { from: range.value[0], to: range.value[1] } })
  await nextTick()
  charts.forEach(c => c.dispose())
  charts = [echarts.init(trendEl.value), echarts.init(pyrEl.value)]
  const m = d.value.monthly
  charts[0].setOption({ title: { text: '隐患与事件', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 45, right: 20, bottom: 30 }, xAxis: { data: m.map(x => x.period) }, yAxis: { type: 'value' },
    series: [{ name: '隐患', type: 'bar', data: m.map(x => x.hazards) }, { name: '重大隐患', type: 'bar', data: m.map(x => x.major) },
      { name: '未遂事件', type: 'line', data: m.map(x => x.near_miss) }, { name: '伤害事件', type: 'line', data: m.map(x => x.injuries) }] })
  const order = ['LOST_TIME', 'RECORDABLE', 'FIRST_AID', 'NEAR_MISS']
  const p = order.map(t => ({ name: TYPES[t], value: (d.value.pyramid.find(x => x.type === t) || { n: 0 }).n }))
  charts[1].setOption({ title: { text: '事故金字塔（海因里希）', textStyle: { fontSize: 13 } }, tooltip: {},
    series: [{ type: 'funnel', sort: 'ascending', top: 40, bottom: 10, minSize: '15%', label: { formatter: '{b} {c}' }, data: p }] })
}
onMounted(load)
const resize = () => charts.forEach(c => c.resize())
window.addEventListener('resize', resize)
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach(c => c.dispose()) })
</script>

<style scoped>
.kpis { display: flex; gap: 12px; margin: 8px 0 12px; flex-wrap: wrap; }
.kpi { flex: 1; min-width: 140px; background: #f6f8fb; border-radius: 6px; padding: 10px 14px; }
.kpi .v { font-size: 20px; font-weight: 700; color: #1f3864; }
.kpi .l { color: #7a8599; font-size: 12px; margin-top: 4px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chart { height: 300px; }
h4 { margin: 16px 0 8px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
</style>
