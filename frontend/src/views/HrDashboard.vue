<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false" @change="load" />
        <span class="muted">人数与流失来自员工档案，出勤来自考勤，人工成本来自工资表（应发 + 单位社保公积金）</span>
        <span class="spacer" />
        <el-dropdown @command="q => $router.push({ path: '/copilot', query: { q } })">
          <el-button type="primary" plain>问一问</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="q in askList" :key="q" :command="q">{{ q }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
      <div class="kpis" v-if="d">
        <div class="kpi"><div class="v">{{ d.headcount }}</div><div class="l">月末在职</div></div>
        <div class="kpi"><div class="v">{{ d.hires }} / {{ d.leaves }}</div><div class="l">本月入职 / 离职</div></div>
        <div class="kpi"><div class="v">{{ pct(turnover) }}</div><div class="l">本月流失率</div></div>
        <div class="kpi"><div class="v">{{ pct(d.attendanceRate) }}</div><div class="l">出勤率</div></div>
        <div class="kpi"><div class="v">{{ wan(monthCost) }}</div><div class="l">本月人工成本</div></div>
      </div>
      <div class="grid">
        <div ref="trendEl" class="chart" />
        <div ref="costEl" class="chart" />
        <div ref="deptEl" class="chart" />
        <div ref="lineEl" class="chart" />
      </div>
      <h4>产线人工（{{ d?.month }}）</h4>
      <el-table :data="d?.lines || []" size="small" border>
        <el-table-column prop="line_code" label="产线" width="70" />
        <el-table-column prop="workers" label="计薪人数" align="right" />
        <el-table-column label="人均应发" align="right"><template #default="{ row }">{{ money(row.avg_gross) }}</template></el-table-column>
        <el-table-column label="人均加班(h)" align="right"><template #default="{ row }">{{ row.ot_per_worker }}</template></el-table-column>
        <el-table-column label="良品数" align="right"><template #default="{ row }">{{ qty(row.good_qty) }}</template></el-table-column>
        <el-table-column label="人工成本" align="right"><template #default="{ row }">{{ money(row.labor_cost) }}</template></el-table-column>
        <el-table-column label="单位人工成本(元/台)" align="right"><template #default="{ row }">{{ row.good_qty ? (row.labor_cost / row.good_qty).toFixed(2) : '' }}</template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'
import { money, pct, qty, wan } from '../api/format'

const month = ref(null)
const d = ref(null)
const trendEl = ref(), costEl = ref(), deptEl = ref(), lineEl = ref()
let charts = []
const COST = { PRODUCTION: '制造费用', SALES: '销售费用', ADMIN: '管理费用', RND: '研发费用' }
/** 带着当前月份去问 Copilot；看板和问数用的是同一套口径，数字应该一致 */
const askList = computed(() => [`${month.value} 各部门出勤率`, `${month.value} 各产线加班率`, `${month.value} 各部门人工成本`,
  `${month.value} 离职率是多少`, `${month.value} 离职人员名单`, `${month.value} 各产线单位人工成本`])
const turnover = computed(() => d.value?.trend.find(t => t.month === d.value.month)?.turnover)
const monthCost = computed(() => (d.value?.laborCost || []).filter(c => c.period === d.value.month).reduce((s, c) => s + Number(c.cost), 0))

async function load() {
  d.value = await http.get('/hr/dashboard', { params: { month: month.value || undefined } })
  month.value = d.value.month
  await nextTick()
  charts.forEach(c => c.dispose())
  charts = [trendEl, costEl, deptEl, lineEl].map(el => echarts.init(el.value))
  const t = d.value.trend
  charts[0].setOption({ title: { text: '人数与流失', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 45, right: 45, bottom: 30 }, xAxis: { data: t.map(x => x.month) },
    yAxis: [{ type: 'value', name: '人' }, { type: 'value', name: '流失率', axisLabel: { formatter: v => (v * 100).toFixed(0) + '%' } }],
    series: [{ name: '在职', type: 'bar', data: t.map(x => x.headcount) }, { name: '入职', type: 'line', data: t.map(x => x.hires) },
      { name: '离职', type: 'line', data: t.map(x => x.leaves) }, { name: '流失率', type: 'line', yAxisIndex: 1, data: t.map(x => x.turnover) }] })
  const periods = [...new Set(d.value.laborCost.map(c => c.period))]
  charts[1].setOption({ title: { text: '人工成本（按费用归集）', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 60, right: 20, bottom: 30 }, xAxis: { data: periods }, yAxis: { axisLabel: { formatter: v => (v / 1e4).toFixed(0) + '万' } },
    series: Object.keys(COST).map(k => ({ name: COST[k], type: 'bar', stack: 'c', data: periods.map(p => Number(d.value.laborCost.find(c => c.period === p && c.cost_type === k)?.cost || 0)) })) })
  charts[2].setOption({ title: { text: '部门人数', textStyle: { fontSize: 13 } }, tooltip: {},
    series: [{ type: 'pie', radius: ['35%', '65%'], top: 20, data: d.value.byDept.map(x => ({ name: x.dept, value: x.headcount })) }] })
  const lines = d.value.lines
  charts[3].setOption({ title: { text: '产线人均应发与加班', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 50, right: 45, bottom: 30 }, xAxis: { data: lines.map(l => l.line_code) },
    yAxis: [{ type: 'value', name: '元' }, { type: 'value', name: '小时' }],
    series: [{ name: '人均应发', type: 'bar', data: lines.map(l => l.avg_gross) }, { name: '人均加班', type: 'line', yAxisIndex: 1, data: lines.map(l => l.ot_per_worker) }] })
}
const resize = () => charts.forEach(c => c.resize())
onMounted(() => { load(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach(c => c.dispose()) })
</script>

<style scoped>
.kpis { display: flex; gap: 12px; margin: 8px 0 16px; flex-wrap: wrap; }
.kpi { flex: 1; min-width: 140px; background: #f6f8fb; border-radius: 6px; padding: 12px 16px; }
.kpi .v { font-size: 22px; font-weight: 700; color: #1f3864; }
.kpi .l { color: #7a8599; font-size: 12px; margin-top: 4px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chart { height: 300px; }
h4 { margin: 16px 0 8px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
</style>
