<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="start" type="date" value-format="YYYY-MM-DD" :clearable="false" @change="load" />
        <span class="muted">安全余额 {{ wan(safety) }}｜应收按客户近 6 个月平均逾期天数推算回款日，应付按到期日，工资 10 日、税款 15 日、出口退税约 60 天到账</span>
      </div>
      <div v-if="f" class="kpis">
        <div class="kpi"><div class="v">{{ wan(f.opening) }}</div><div class="l">期初银行存款</div></div>
        <div class="kpi"><div class="v" :style="{ color: f.minBalance < safety ? '#c0392b' : '#3d8c5a' }">{{ wan(f.minBalance) }}</div><div class="l">最低余额（{{ f.minWeek }} 周）</div></div>
        <div class="kpi"><div class="v">{{ f.firstBelowSafety || '无' }}</div><div class="l">首次低于安全余额</div></div>
      </div>
      <div ref="chartEl" class="chart" />
      <el-table :data="f?.weeks || []" size="small" border>
        <el-table-column prop="index" label="周" width="50" /><el-table-column prop="start" label="开始" width="100" />
        <el-table-column label="流入" align="right"><template #default="{ row }">{{ money(row.inflow) }}</template></el-table-column>
        <el-table-column label="流出" align="right"><template #default="{ row }">{{ money(row.outflow) }}</template></el-table-column>
        <el-table-column label="期末余额" align="right"><template #default="{ row }">{{ money(row.ending) }}</template></el-table-column>
        <el-table-column label="构成" min-width="300"><template #default="{ row }">
          <span v-for="(v, k) in row.byCategory" :key="k" class="cat">{{ k }} {{ wan(v) }}</span></template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'
import { money, wan } from '../api/format'

const start = ref('2026-09-28')
const safety = 10000000
const f = ref(null)
const chartEl = ref()
let chart
async function load() {
  f.value = await http.get('/treasury/forecast', { params: { start: start.value, weeks: 13, safety } })
  await nextTick()
  chart?.dispose()
  chart = echarts.init(chartEl.value)
  const w = f.value.weeks
  chart.setOption({ tooltip: { trigger: 'axis' }, legend: { top: 0 }, grid: { top: 40, left: 70, right: 20, bottom: 30 }, xAxis: { data: w.map(x => 'W' + x.index) },
    yAxis: { axisLabel: { formatter: v => (v / 1e4).toFixed(0) + '万' } },
    series: [{ name: '流入', type: 'bar', data: w.map(x => x.inflow) }, { name: '流出', type: 'bar', data: w.map(x => -x.outflow) },
      { name: '期末余额', type: 'line', data: w.map(x => x.ending), markLine: { symbol: 'none', data: [{ yAxis: safety, label: { formatter: '安全余额' } }] } }] })
}
onMounted(load)
onBeforeUnmount(() => chart?.dispose())
</script>

<style scoped>
.kpis { display: flex; gap: 12px; margin: 8px 0 12px; flex-wrap: wrap; }
.kpi { flex: 1; min-width: 150px; background: #f6f8fb; border-radius: 6px; padding: 10px 14px; }
.kpi .v { font-size: 20px; font-weight: 700; color: #1f3864; }
.kpi .l { color: #7a8599; font-size: 12px; margin-top: 4px; }
.chart { height: 300px; margin-bottom: 12px; }
.cat { margin-right: 10px; font-size: 12px; color: #55607a; }
</style>
