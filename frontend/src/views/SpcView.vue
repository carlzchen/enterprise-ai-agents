<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-select v-model="product" filterable style="width: 190px" @change="load"><el-option v-for="p in products" :key="p" :label="p" :value="p" /></el-select>
        <el-select v-model="ch" style="width: 150px" @change="load"><el-option v-for="c in chars" :key="c.characteristic" :label="c.char_name" :value="c.characteristic" /></el-select>
        <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" :clearable="false" @change="load" style="width: 250px" />
        <span class="muted">每天抽前 5 台出厂测试值为一个子组</span>
      </div>
      <div v-if="r" class="kpis">
        <div class="kpi"><div class="v" :style="{ color: color(r.cpk) }">{{ r.cpk ?? '-' }}</div><div class="l">Cpk（组内）</div></div>
        <div class="kpi"><div class="v">{{ r.ppk ?? '-' }}</div><div class="l">Ppk（整体）</div></div>
        <div class="kpi"><div class="v">{{ fmt(r.center) }}</div><div class="l">X̄̄（规格 {{ spec }}）</div></div>
        <div class="kpi"><div class="v">{{ fmt(r.sigma) }}</div><div class="l">σ 组内</div></div>
        <div class="kpi"><div class="v">{{ pct(r.failRate, 2) }}</div><div class="l">测试不合格率</div></div>
        <div class="kpi"><div class="v" :style="{ color: r.violations.length ? '#c0392b' : '#3d8c5a' }">{{ r.violations.length }}</div><div class="l">判异点</div></div>
      </div>
      <div ref="xEl" class="chart" />
      <div ref="rEl" class="chart small" />
      <div ref="trendEl" class="chart small" />
      <el-table v-if="r?.violations.length" :data="r.violations.slice(0, 50)" size="small" border max-height="240">
        <el-table-column prop="date" label="子组日期" width="120" />
        <el-table-column prop="rule" label="判异规则" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'
import { pct } from '../api/format'

const specs = ref([])
const product = ref('KL-SV-80-750W')
const ch = ref('back_emf')
const range = ref(['2026-01-01', '2026-09-28'])
const r = ref(null)
const xEl = ref(), rEl = ref(), trendEl = ref()
let charts = []
const products = computed(() => [...new Set(specs.value.map(s => s.code))])
const chars = computed(() => specs.value.filter(s => s.code === product.value))
const spec = computed(() => { const s = r.value?.spec; return s ? `${s.lsl ?? '—'} ~ ${s.usl ?? '—'} ${s.unit}` : '' })
const fmt = v => v == null ? '-' : Number(v).toFixed(3)
const color = v => v == null ? '' : v >= 1.33 ? '#3d8c5a' : v >= 1.0 ? '#d08a1e' : '#c0392b'

async function load() {
  r.value = await http.get('/qms/spc', { params: { product: product.value, characteristic: ch.value, from: range.value[0], to: range.value[1] } })
  const trend = await http.get('/qms/spc/cpk-trend', { params: { product: product.value, characteristic: ch.value, from: '2024-10-01', to: '2026-09-28' } })
  await nextTick()
  charts.forEach(c => c.dispose())
  charts = [echarts.init(xEl.value), echarts.init(rEl.value), echarts.init(trendEl.value)]
  const days = r.value.days.map(d => String(d).slice(5))
  const bad = new Set(r.value.violations.map(v => String(v.date).slice(5)))
  const line = (name, v, c) => ({ name, type: 'line', data: days.map(() => v), showSymbol: false, lineStyle: { type: 'dashed', color: c }, itemStyle: { color: c } })
  const s = r.value.spec
  charts[0].setOption({ title: { text: 'X̄ 控制图', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 55, right: 20, bottom: 30 }, xAxis: { data: days }, yAxis: { scale: true },
    series: [{ name: 'X̄', type: 'line', data: r.value.xbar.map((v, i) => ({ value: v, itemStyle: { color: bad.has(days[i]) ? '#c0392b' : '#3a6fb0' } })), symbolSize: 5 },
      line('UCL', r.value.ucl, '#c0392b'), line('CL', r.value.center, '#3d8c5a'), line('LCL', r.value.lcl, '#c0392b'),
      ...(s.usl != null ? [line('USL', Number(s.usl), '#888')] : []), ...(s.lsl != null ? [line('LSL', Number(s.lsl), '#888')] : [])] })
  charts[1].setOption({ title: { text: 'R 控制图', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, grid: { top: 40, left: 55, right: 20, bottom: 30 },
    xAxis: { data: days }, yAxis: { scale: true },
    series: [{ name: 'R', type: 'line', data: r.value.range, symbolSize: 4 }, line('UCL', r.value.rUcl, '#c0392b'), line('R̄', r.value.rBar, '#3d8c5a')] })
  charts[2].setOption({ title: { text: '月度 Cpk 与不合格率', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 0, right: 10 },
    grid: { top: 40, left: 55, right: 55, bottom: 30 }, xAxis: { data: trend.map(t => t.month) },
    yAxis: [{ type: 'value', name: 'Cpk' }, { type: 'value', name: '不合格率', axisLabel: { formatter: v => (v * 100).toFixed(1) + '%' } }],
    series: [{ name: 'Cpk', type: 'bar', data: trend.map(t => ({ value: t.cpk, itemStyle: { color: color(t.cpk) } })),
      markLine: { symbol: 'none', data: [{ yAxis: 1.33, label: { formatter: '1.33' } }, { yAxis: 1.0, label: { formatter: '1.0' } }] } },
      { name: '不合格率', type: 'line', yAxisIndex: 1, data: trend.map(t => t.failRate) }] })
}
const resize = () => charts.forEach(c => c.resize())
onMounted(async () => { specs.value = await http.get('/qms/spc/products'); await load(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach(c => c.dispose()) })
</script>

<style scoped>
.kpis { display: flex; gap: 12px; margin: 8px 0 12px; flex-wrap: wrap; }
.kpi { flex: 1; min-width: 110px; background: #f6f8fb; border-radius: 6px; padding: 10px 14px; }
.kpi .v { font-size: 20px; font-weight: 700; color: #1f3864; }
.kpi .l { color: #7a8599; font-size: 12px; margin-top: 4px; }
.chart { height: 300px; }
.chart.small { height: 220px; margin-top: 8px; }
</style>
