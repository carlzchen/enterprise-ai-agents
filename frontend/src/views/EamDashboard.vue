<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar"><span class="muted">近 12 个月：故障次数与停机时长、保养次数、备件费用（计入制造费用）；停机 TOP 设备与故障代码分布</span></div>
      <div class="grid">
        <div ref="trendEl" class="chart" />
        <div ref="faultEl" class="chart" />
      </div>
      <h4>停机 TOP 10 设备</h4>
      <el-table :data="d?.topDowntime || []" size="small" border>
        <el-table-column prop="code" label="设备编码" width="150" />
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column prop="line_code" label="产线" width="70" />
        <el-table-column prop="breakdowns" label="故障次数" width="90" align="right" />
        <el-table-column prop="downtime_min" label="停机(分)" width="100" align="right" />
        <el-table-column prop="avg_repair_min" label="平均修复(分)" width="110" align="right" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'

const d = ref(null)
const trendEl = ref(), faultEl = ref()
let charts = []
onMounted(async () => {
  d.value = await http.get('/eam/dashboard', { params: { to: '2026-09-28' } })
  await nextTick()
  charts = [echarts.init(trendEl.value), echarts.init(faultEl.value)]
  const m = d.value.monthly
  charts[0].setOption({ title: { text: '故障与停机', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 50, right: 50, bottom: 30 }, xAxis: { data: m.map(x => x.month) },
    yAxis: [{ type: 'value', name: '次' }, { type: 'value', name: '停机(小时)' }],
    series: [{ name: '故障', type: 'bar', data: m.map(x => x.breakdowns) }, { name: '保养', type: 'bar', data: m.map(x => x.pms) },
      { name: '故障停机(小时)', type: 'line', yAxisIndex: 1, data: m.map(x => Math.round(x.breakdown_min / 6) / 10) }] })
  charts[1].setOption({ title: { text: '故障代码分布', textStyle: { fontSize: 13 } }, tooltip: {},
    series: [{ type: 'pie', radius: ['35%', '65%'], top: 20, data: d.value.faultCodes.map(x => ({ name: x.fault_code, value: x.n })) }] })
})
const resize = () => charts.forEach(c => c.resize())
window.addEventListener('resize', resize)
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach(c => c.dispose()) })
</script>

<style scoped>
.grid { display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; }
.chart { height: 300px; }
h4 { margin: 16px 0 8px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
</style>
