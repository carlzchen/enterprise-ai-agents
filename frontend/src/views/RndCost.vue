<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="range" type="monthrange" value-format="YYYY-MM" :clearable="false" @change="load" />
        <span class="muted">研发中心员工人工成本（应发 + 单位社保公积金）× 项目工时 / 出勤工时；未登记到项目的计入"非项目"。可作为研发费用加计扣除的项目辅助账。</span>
      </div>
      <div ref="chartEl" class="chart" />
      <el-table :data="rows" size="small" border show-summary :summary-method="summary">
        <el-table-column prop="key" label="项目" width="80" fixed />
        <el-table-column prop="name" label="名称" min-width="200" />
        <el-table-column label="工时(h)" width="100" align="right"><template #default="{ row }">{{ row.hours ? Number(row.hours).toFixed(0) : '' }}</template></el-table-column>
        <el-table-column label="人工成本" width="130" align="right"><template #default="{ row }">{{ money(row.cost) }}</template></el-table-column>
        <el-table-column label="占比" width="80" align="right"><template #default="{ row }">{{ pct(row.cost / total) }}</template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'
import { money, pct } from '../api/format'

const range = ref(['2025-10', '2026-08'])
const rows = ref([])
const total = ref(1)
const chartEl = ref()
let chart
async function load() {
  const r = await http.get('/pm/rnd-cost', { params: { from: range.value[0], to: range.value[1] } })
  const names = Object.fromEntries(r.projects.map(p => [p.project_key, p.name]))
  rows.value = Object.entries(r.total).map(([key, cost]) => ({ key, name: names[key] || key, cost: Number(cost), hours: r.hours[key] })).sort((a, b) => b.cost - a.cost)
  total.value = rows.value.reduce((s, x) => s + x.cost, 0) || 1
  await nextTick()
  chart?.dispose()
  chart = echarts.init(chartEl.value)
  const keys = rows.value.map(x => x.key)
  chart.setOption({ title: { text: '研发人工成本按项目（月）', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 60, right: 20, bottom: 30 }, xAxis: { data: r.months.map(m => m.month) }, yAxis: { axisLabel: { formatter: v => (v / 1e4).toFixed(0) + '万' } },
    series: keys.map(k => ({ name: k, type: 'bar', stack: 'c', data: r.months.map(m => Number(m.allocation[k] || 0)) })) })
}
const summary = ({ columns }) => columns.map((c, i) => i === 0 ? '合计' : i === 3 ? money(total.value) : i === 2 ? rows.value.reduce((s, x) => s + Number(x.hours || 0), 0).toFixed(0) : '')
onMounted(load)
onBeforeUnmount(() => chart?.dispose())
</script>

<style scoped>
.chart { height: 300px; margin-bottom: 12px; }
</style>
