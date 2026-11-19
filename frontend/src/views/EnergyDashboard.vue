<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="period" type="month" value-format="YYYY-MM" :clearable="false" @change="load" />
        <span class="muted">广东分时电价：峰 10–12、14–19 点，谷 0–8 点，峰平谷 1.7 : 1 : 0.38，7–9 月 11–12、15–17 点尖峰（峰 × 1.25）；碳排放因子 0.5366 kgCO₂/kWh</span>
      </div>
      <div v-if="b" class="kpis">
        <div class="kpi"><div class="v">{{ (b.total_kwh / 1e4).toFixed(1) }} 万kWh</div><div class="l">用电量</div></div>
        <div class="kpi"><div class="v">{{ wan(b.total_amount) }}</div><div class="l">电费 + 水费（基本电费 {{ wan(b.capacity_charge) }}）</div></div>
        <div class="kpi"><div class="v">{{ Number(b.avg_price).toFixed(3) }}</div><div class="l">综合电价（元/kWh）</div></div>
        <div class="kpi"><div class="v">{{ pct((Number(b.peak_kwh) + Number(b.sharp_kwh)) / b.total_kwh) }}</div><div class="l">尖峰 + 峰段占比（谷 {{ pct(b.valley_kwh / b.total_kwh) }}）</div></div>
        <div class="kpi"><div class="v">{{ Number(b.kwh_per_unit).toFixed(3) }}</div><div class="l">单位产品电耗（kWh/台）</div></div>
        <div class="kpi"><div class="v">{{ Number(b.carbon_t).toFixed(0) }} t</div><div class="l">碳排放 CO₂</div></div>
      </div>
      <el-alert v-else type="info" :closable="false" title="该月尚未出账（次月第一个工作日出账）" style="margin-bottom: 10px" />
      <div class="grid">
        <div ref="billEl" class="chart" />
        <div ref="airEl" class="chart" />
      </div>
      <div class="grid">
        <div>
          <h4>各电表（当月）</h4>
          <el-table :data="d?.meters || []" size="small" border>
            <el-table-column prop="name" label="电表" min-width="150" />
            <el-table-column label="电量(kWh)" align="right"><template #default="{ row }">{{ qty(row.total_kwh) }}</template></el-table-column>
            <el-table-column label="峰段占比" align="right" width="90"><template #default="{ row }">{{ pct(row.peak_kwh / row.total_kwh) }}</template></el-table-column>
            <el-table-column label="谷段占比" align="right" width="90"><template #default="{ row }">{{ pct(row.valley_kwh / row.total_kwh) }}</template></el-table-column>
          </el-table>
        </div>
        <div>
          <h4>产线单位产品电耗（当月）</h4>
          <el-table :data="d?.lines || []" size="small" border>
            <el-table-column prop="line_code" label="产线" width="70" />
            <el-table-column label="电量(kWh)" align="right"><template #default="{ row }">{{ qty(row.kwh) }}</template></el-table-column>
            <el-table-column label="良品(台)" align="right"><template #default="{ row }">{{ qty(row.good_qty) }}</template></el-table-column>
            <el-table-column prop="kwh_per_unit" label="kWh/台" align="right" />
          </el-table>
          <div ref="lineEl" class="chart" style="margin-top: 12px" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'
import { pct, qty, wan } from '../api/format'

const period = ref('2026-08')
const d = ref(null)
const b = computed(() => d.value?.bill)
const billEl = ref(), airEl = ref(), lineEl = ref()
let charts = []
async function load() {
  d.value = await http.get('/energy/dashboard', { params: { period: period.value } })
  await nextTick()
  charts.forEach(c => c.dispose())
  charts = [echarts.init(billEl.value), echarts.init(airEl.value), echarts.init(lineEl.value)]
  const bs = d.value.bills
  charts[0].setOption({ title: { text: '月度电量与电费', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 55, right: 55, bottom: 30 }, xAxis: { data: bs.map(x => x.period) },
    yAxis: [{ type: 'value', name: '万kWh' }, { type: 'value', name: '万元' }],
    series: [
      { name: '尖峰', type: 'bar', stack: 'k', data: bs.map(x => ((Number(x.peak_kwh) + Number(x.sharp_kwh)) / 1e4).toFixed(1)) },
      { name: '平', type: 'bar', stack: 'k', data: bs.map(x => (x.flat_kwh / 1e4).toFixed(1)) },
      { name: '谷', type: 'bar', stack: 'k', data: bs.map(x => (x.valley_kwh / 1e4).toFixed(1)) },
      { name: '账单金额', type: 'line', yAxisIndex: 1, data: bs.map(x => (x.total_amount / 1e4).toFixed(1)) }] })
  const a = d.value.airBaseline
  charts[1].setOption({ title: { text: '空压站非生产日空载电量（kWh/天，抬升 = 管网泄漏）', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' },
    grid: { top: 40, left: 50, right: 20, bottom: 30 }, xAxis: { data: a.map(x => x.period) }, yAxis: { type: 'value' },
    series: [{ type: 'line', areaStyle: {}, data: a.map(x => x.idle_day_kwh) }] })
  const lt = d.value.lineTrend, periods = [...new Set(lt.map(x => x.period))], lines = [...new Set(lt.map(x => x.line_code))]
  charts[2].setOption({ title: { text: '单位产品电耗趋势（kWh/台）', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 45, right: 20, bottom: 30 }, xAxis: { data: periods }, yAxis: { type: 'log' },
    series: lines.map(l => ({ name: l, type: 'line', data: periods.map(p => (lt.find(x => x.line_code === l && x.period === p) || {}).kwh_per_unit) })) })
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
