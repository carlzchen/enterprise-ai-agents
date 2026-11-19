<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="date" type="date" value-format="YYYY-MM-DD" :clearable="false" @change="load" />
        <span class="muted">OEE = 时间开动率（设备停机损失）× 性能开动率（产出 / 理论产能）× 合格品率；劳动效率 = 产出折算标准工时 / 产线出勤工时</span>
      </div>
      <div class="cards">
        <div v-for="l in d?.lines || []" :key="l.line_code" class="card">
          <div class="head"><b>{{ l.line_code }}</b> <span class="muted">{{ l.name }}</span></div>
          <div class="oee" :style="{ color: l.oee >= 0.65 ? '#3d8c5a' : l.oee >= 0.5 ? '#d08a1e' : '#c0392b' }">{{ pct(l.oee) }}</div>
          <div class="row"><span>A {{ pct(l.availability) }}</span><span>P {{ pct(l.performance) }}</span><span>Q {{ pct(l.quality, 2) }}</span></div>
          <div class="row"><span>产出 {{ qty(l.good_qty) }}</span><span>效率 {{ pct(l.labor_efficiency) }}</span></div>
          <div class="row"><span :class="{ warn: l.loss_min }">停机损失 {{ l.loss_min }} 分</span><span :class="{ warn: l.andons }">安灯 {{ l.andons }}</span>
            <span :class="{ warn: l.ipqc_fail }">IPQC 不合格 {{ l.ipqc_fail }}</span></div>
        </div>
      </div>
      <div class="grid">
        <div ref="trendEl" class="chart" />
        <div ref="monthEl" class="chart" />
      </div>
      <h4>未关闭安灯</h4>
      <el-table :data="d?.openAndons || []" size="small" border empty-text="没有未关闭的安灯">
        <el-table-column prop="line_code" label="产线" width="70" />
        <el-table-column label="类型" width="80"><template #default="{ row }">{{ ANDON[row.type] }}</template></el-table-column>
        <el-table-column prop="description" label="描述" min-width="260" />
        <el-table-column prop="ref_no" label="单据" width="130" />
        <el-table-column label="呼叫时间" width="150"><template #default="{ row }">{{ String(row.raised_at).replace('T', ' ').slice(0, 16) }}</template></el-table-column>
        <el-table-column prop="response_min" label="响应(分)" width="90" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'
import { pct, qty } from '../api/format'

const ANDON = { MATERIAL: '缺料', EQUIPMENT: '设备', QUALITY: '质量' }
const date = ref(null)
const d = ref(null)
const trendEl = ref(), monthEl = ref()
let charts = []

async function load() {
  d.value = await http.get('/mes/board', { params: { date: date.value || undefined } })
  date.value = d.value.date
  await nextTick()
  charts.forEach(c => c.dispose())
  charts = [echarts.init(trendEl.value), echarts.init(monthEl.value)]
  const lines = [...new Set(d.value.trend.map(t => t.line_code))]
  const days = [...new Set(d.value.trend.map(t => t.work_date))]
  charts[0].setOption({ title: { text: '近 45 天日 OEE', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis', valueFormatter: v => (v * 100).toFixed(1) + '%' },
    legend: { top: 22 }, grid: { top: 60, left: 45, right: 20, bottom: 30 }, xAxis: { data: days.map(x => String(x).slice(5)) },
    yAxis: { min: 0, max: 1, axisLabel: { formatter: v => v * 100 + '%' } },
    series: lines.map(l => ({ name: l, type: 'line', showSymbol: false, data: days.map(x => d.value.trend.find(t => t.line_code === l && t.work_date === x)?.oee ?? null) })) })
  const months = [...new Set(d.value.monthlyOee.map(t => t.month))]
  charts[1].setOption({ title: { text: '月度平均 OEE', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis', valueFormatter: v => (v * 100).toFixed(1) + '%' },
    legend: { top: 22 }, grid: { top: 60, left: 45, right: 20, bottom: 30 }, xAxis: { data: months },
    yAxis: { min: 0, max: 1, axisLabel: { formatter: v => v * 100 + '%' } },
    series: lines.map(l => ({ name: l, type: 'line', data: months.map(m => d.value.monthlyOee.find(t => t.line_code === l && t.month === m)?.oee ?? null) })) })
}
const resize = () => charts.forEach(c => c.resize())
onMounted(() => { load(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach(c => c.dispose()) })
</script>

<style scoped>
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; margin: 8px 0 16px; }
.card { background: #f6f8fb; border-radius: 6px; padding: 10px 14px; }
.head { margin-bottom: 4px; }
.oee { font-size: 26px; font-weight: 700; }
.row { display: flex; gap: 10px; font-size: 12px; color: #55607a; margin-top: 3px; flex-wrap: wrap; }
.warn { color: #c0392b; font-weight: 600; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.chart { height: 300px; }
h4 { margin: 16px 0 8px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
</style>
