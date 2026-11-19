<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" :clearable="false" @change="load" />
        <span class="muted">优先级 = 影响范围 × 紧急程度；P1 按 7×24 计时（响应 0.5h / 解决 4h），P2~P4 按工作时间计时</span>
      </div>
      <div v-if="d" class="kpis">
        <div class="kpi"><div class="v">{{ d.summary.incidents }} / {{ d.summary.requests }} / {{ d.summary.changes }}</div><div class="l">故障 / 服务请求 / 变更</div></div>
        <div class="kpi"><div class="v" :style="{ color: d.summary.sla_rate < 0.9 ? '#c0392b' : '#3d8c5a' }">{{ pct(d.summary.sla_rate) }}</div><div class="l">SLA 达成率</div></div>
        <div class="kpi"><div class="v">{{ d.summary.mttr_h }} h</div><div class="l">故障平均解决时长（MTTR）</div></div>
        <div class="kpi"><div class="v">{{ d.summary.p1 }}</div><div class="l">P1 重大故障</div></div>
        <div class="kpi"><div class="v">{{ d.summary.csat }}</div><div class="l">满意度</div></div>
        <div class="kpi"><div class="v">{{ d.summary.backlog }}</div><div class="l">未完成工单</div></div>
      </div>
      <div class="grid">
        <div ref="trendEl" class="chart" />
        <div ref="catEl" class="chart" />
      </div>
      <h4>重大故障与关联变更</h4>
      <el-table :data="d?.majorIncidents || []" size="small" border>
        <el-table-column prop="ticket_no" label="故障单" width="140" /><el-table-column prop="title" label="故障" min-width="220" />
        <el-table-column prop="created_at" label="发生" width="150" /><el-table-column prop="resolved_at" label="恢复" width="150" />
        <el-table-column prop="root_cause" label="根因" min-width="220" /><el-table-column prop="change_title" label="变更" min-width="220" />
        <el-table-column prop="change_status" label="变更状态" width="90" />
      </el-table>
      <div class="grid">
        <div>
          <h4>按优先级</h4>
          <el-table :data="d?.byPriority || []" size="small" border>
            <el-table-column prop="priority" label="优先级" width="70" /><el-table-column prop="n" label="工单" align="right" />
            <el-table-column prop="breached" label="超时" align="right" /><el-table-column prop="avg_resolve_h" label="平均解决(h)" align="right" />
          </el-table>
          <h4>CMDB</h4>
          <el-table :data="d?.cmdb || []" size="small" border>
            <el-table-column prop="type" label="类型" /><el-table-column prop="in_use" label="在用" align="right" /><el-table-column prop="stock" label="库存" align="right" />
            <el-table-column prop="retired" label="报废" align="right" /><el-table-column prop="over_4y" label="在用超 4 年" align="right" />
          </el-table>
        </div>
        <div>
          <h4>软件许可</h4>
          <el-table :data="d?.licenses || []" size="small" border>
            <el-table-column prop="name" label="软件" min-width="160" /><el-table-column prop="scope" label="范围" width="60" />
            <el-table-column prop="seats" label="席位" align="right" width="60" /><el-table-column prop="used" label="已用" align="right" width="60" />
            <el-table-column prop="waiting" label="待分配" align="right" width="70" />
            <el-table-column label="年费" align="right"><template #default="{ row }">{{ money(row.annual_cost) }}</template></el-table-column>
            <el-table-column label="状态" width="80"><template #default="{ row }"><el-tag size="small" :type="row.status === 'SHORTAGE' ? 'danger' : row.status === 'SURPLUS' ? 'warning' : 'success'">
              {{ { SHORTAGE: '不足', SURPLUS: '富余', OK: '正常' }[row.status] }}</el-tag></template></el-table-column>
          </el-table>
          <h4>故障最多的配置项</h4>
          <el-table :data="d?.topCis || []" size="small" border>
            <el-table-column prop="ci_no" label="编号" width="90" /><el-table-column prop="name" label="名称" /><el-table-column prop="incidents" label="故障" align="right" />
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
import { money, pct } from '../api/format'

const range = ref(['2025-10-01', '2026-09-28'])
const d = ref(null)
const trendEl = ref(), catEl = ref()
let charts = []
async function load() {
  d.value = await http.get('/itsm/dashboard', { params: { from: range.value[0], to: range.value[1] } })
  await nextTick()
  charts.forEach(c => c.dispose())
  charts = [echarts.init(trendEl.value), echarts.init(catEl.value)]
  const m = d.value.monthly
  charts[0].setOption({ title: { text: '月度工单', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 45, right: 20, bottom: 30 }, xAxis: { data: m.map(x => x.period) }, yAxis: { type: 'value' },
    series: [{ name: '故障', type: 'bar', stack: 't', data: m.map(x => x.incidents) }, { name: '服务请求', type: 'bar', stack: 't', data: m.map(x => x.requests) },
      { name: '变更', type: 'bar', stack: 't', data: m.map(x => x.changes) }, { name: '超时', type: 'line', data: m.map(x => x.breached) }] })
  charts[1].setOption({ title: { text: '分类', textStyle: { fontSize: 13 } }, tooltip: {},
    series: [{ type: 'pie', radius: ['35%', '65%'], top: 20, data: d.value.byCategory.map(x => ({ name: x.category, value: x.n })) }] })
}
onMounted(load)
const resize = () => charts.forEach(c => c.resize())
window.addEventListener('resize', resize)
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach(c => c.dispose()) })
</script>

<style scoped>
.kpis { display: flex; gap: 12px; margin: 8px 0 12px; flex-wrap: wrap; }
.kpi { flex: 1; min-width: 150px; background: #f6f8fb; border-radius: 6px; padding: 10px 14px; }
.kpi .v { font-size: 20px; font-weight: 700; color: #1f3864; }
.kpi .l { color: #7a8599; font-size: 12px; margin-top: 4px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chart { height: 300px; }
h4 { margin: 16px 0 8px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
</style>
