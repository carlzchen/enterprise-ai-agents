<template>
  <div class="page dash">
    <div class="toolbar">
      <span>统计月份</span>
      <el-date-picker v-model="month" type="month" value-format="YYYY-MM" :clearable="false" @change="loadAll" />
      <span class="muted">收入按发货确认，人民币不含税；毛利 = 收入 − 出库成本（移动加权平均）</span>
      <span class="spacer" />
      <el-dropdown @command="q => $router.push({ path: '/copilot', query: { q } })">
        <el-button type="primary" plain>问一问</el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="q in [`${month} 各产品系列的收入、成本和毛利率`, `${month} 毛利为什么比上个月变化`, `${month} 毛利贡献最大的十个客户`, '哪些客户回款逾期了', '今年每个月的直接人工成本率']"
                              :key="q" :command="q">{{ q }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <div class="kpis" v-if="k">
      <div class="kpi" v-for="c in cards" :key="c.label">
        <div class="kpi-label">{{ c.label }}</div>
        <div class="kpi-value num">{{ c.value }}</div>
        <div class="kpi-sub" :class="c.subClass">{{ c.sub }}</div>
      </div>
    </div>

    <div class="grid">
      <div class="panel wide"><h3>月度收入与毛利率（近 24 个月）</h3><div ref="trendEl" class="chart" /></div>
      <div class="panel"><h3>分产品系列毛利率趋势</h3><div ref="seriesEl" class="chart" /></div>
      <div class="panel"><h3>本年收入构成（按系列）</h3><div ref="mixEl" class="chart" /></div>
      <div class="panel"><h3>本年前十大客户</h3><div ref="custEl" class="chart" /></div>
      <div class="panel">
        <h3>应收账龄（截至月末）</h3><div ref="agingEl" class="chart short" />
        <el-table :data="aging.topOverdueCustomers" size="small">
          <el-table-column prop="customer_name" label="逾期客户" min-width="180" show-overflow-tooltip />
          <el-table-column label="逾期金额" align="right"><template #default="{ row }">{{ money(row.overdue_amount) }}</template></el-table-column>
          <el-table-column prop="max_overdue_days" label="最长逾期(天)" align="right" width="110" />
        </el-table>
      </div>
      <div class="panel"><h3>各产线良率（近 12 个月）</h3><div ref="yieldEl" class="chart" /></div>
      <div class="panel">
        <h3>供应商来料质量（近 6 个月）</h3>
        <el-table :data="suppliers" size="small" max-height="330">
          <el-table-column prop="supplier_name" label="供应商" min-width="170" show-overflow-tooltip />
          <el-table-column prop="category" label="类别" width="90" />
          <el-table-column prop="lots" label="批次" align="right" width="60" />
          <el-table-column prop="failed_lots" label="判退" align="right" width="60" />
          <el-table-column label="合格率" align="right" width="80">
            <template #default="{ row }"><span :style="{ color: row.pass_rate < 0.9 ? '#d0342c' : '' }">{{ pct(row.pass_rate) }}</span></template>
          </el-table-column>
        </el-table>
      </div>
      <div class="panel wide"><h3>本年费用（按类别、部门）</h3><div ref="expEl" class="chart" /></div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'
import { money, pct, wan, SERIES } from '../api/format'

const month = ref('')
const k = ref(null)
const aging = ref({ buckets: [], topOverdueCustomers: [] })
const suppliers = ref([])
const trendEl = ref(), seriesEl = ref(), mixEl = ref(), custEl = ref(), agingEl = ref(), yieldEl = ref(), expEl = ref()
const charts = []

const PALETTE = ['#3a6ea5', '#e8a33d', '#4f9d69', '#c75b5b', '#8a6fb5', '#4bb3c4', '#9c9c9c']

function growth(cur, base) {
  if (!base || Number(base) === 0) return null
  return (Number(cur) - Number(base)) / Number(base)
}

const cards = computed(() => {
  const x = k.value
  const yoy = growth(x.revenue, x.revenueLastYear)
  const gm = Number(x.revenue) ? Number(x.grossProfit) / Number(x.revenue) : null
  const gmLy = Number(x.revenueLastYear) ? Number(x.grossProfitLastYear) / Number(x.revenueLastYear) : null
  return [
    { label: '当月收入', value: wan(x.revenue), sub: yoy === null ? '' : `同比 ${yoy >= 0 ? '+' : ''}${pct(yoy)}`, subClass: yoy >= 0 ? 'up' : 'down' },
    { label: '当月毛利率', value: pct(gm), sub: gmLy === null ? '' : `去年同期 ${pct(gmLy)}`, subClass: gm >= gmLy ? 'up' : 'down' },
    { label: '本年累计收入', value: wan(x.revenueYtd), sub: '' },
    { label: '在手订单', value: wan(x.backlog), sub: '已确认未发货' },
    { label: '应收余额', value: wan(x.arOutstanding), sub: `其中逾期 ${wan(x.arOverdue)}`, subClass: Number(x.arOverdue) > 0 ? 'down' : '' },
    { label: '应付余额', value: wan(x.apOutstanding), sub: '' },
    { label: '库存金额', value: wan(x.inventoryValue), sub: '原材料 + 成品' },
    { label: '当月生产良率', value: pct(x.yield, 2), sub: '' },
    { label: '当月来料合格率', value: pct(x.iqcPassRate), sub: '' },
    { label: '未关闭客诉', value: x.openComplaints, sub: '' }
  ]
})

function chart(el) {
  const c = echarts.init(el)
  charts.push(c)
  return c
}

function prevMonths(m, n) {
  const [y, mm] = m.split('-').map(Number)
  const d = new Date(y, mm - 1 - n, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

async function loadAll() {
  const m = month.value
  const yearStart = `${m.slice(0, 4)}-01`
  const monthEnd = new Date(Number(m.slice(0, 4)), Number(m.slice(5, 7)), 0)
  const asOf = `${m}-${String(monthEnd.getDate()).padStart(2, '0')}`
  const [kpis, trend, seriesTrend, mix, cust, ag, yields, sup, exp] = await Promise.all([
    http.get('/bi/kpis', { params: { month: m } }),
    http.get('/bi/revenue-trend', { params: { month: m, months: 24 } }),
    http.get('/bi/series-margin-trend', { params: { month: m, months: 24 } }),
    http.get('/bi/revenue-by-series', { params: { from: yearStart, to: m } }),
    http.get('/bi/top-customers', { params: { from: yearStart, to: m } }),
    http.get('/bi/ar-aging', { params: { asOf } }),
    http.get('/bi/yield-by-line', { params: { month: m, months: 12 } }),
    http.get('/bi/supplier-quality', { params: { from: `${prevMonths(m, 5)}-01`, to: asOf } }),
    http.get('/bi/expense-by-category', { params: { from: `${yearStart}-01`, to: asOf } })
  ])
  k.value = kpis
  aging.value = ag
  suppliers.value = sup
  await nextTick()
  charts.forEach(c => c.dispose())
  charts.length = 0

  chart(trendEl.value).setOption({
    color: PALETTE, tooltip: { trigger: 'axis' }, legend: { top: 0 },
    grid: { left: 60, right: 50, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: trend.map(r => r.month) },
    yAxis: [{ type: 'value', axisLabel: { formatter: v => v / 1e6 + 'M' } }, { type: 'value', axisLabel: { formatter: v => (v * 100).toFixed(0) + '%' }, splitLine: { show: false } }],
    series: [
      { name: '内销收入', type: 'bar', stack: 'r', data: trend.map(r => r.domestic_revenue) },
      { name: '外销收入', type: 'bar', stack: 'r', data: trend.map(r => r.export_revenue) },
      { name: '毛利率', type: 'line', yAxisIndex: 1, smooth: true, data: trend.map(r => r.gross_margin), color: '#c75b5b' }
    ]
  })

  const seriesNames = [...new Set(seriesTrend.map(r => r.series))]
  const months = [...new Set(seriesTrend.map(r => r.month))]
  chart(seriesEl.value).setOption({
    color: PALETTE, tooltip: { trigger: 'axis', valueFormatter: v => pct(v) }, legend: { top: 0, type: 'scroll' },
    grid: { left: 50, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: months },
    yAxis: { type: 'value', axisLabel: { formatter: v => (v * 100).toFixed(0) + '%' } },
    series: seriesNames.map(s => ({
      name: SERIES[s] || s, type: 'line', smooth: true, showSymbol: false,
      data: months.map(mm => seriesTrend.find(r => r.month === mm && r.series === s)?.gross_margin ?? null)
    }))
  })

  chart(mixEl.value).setOption({
    color: PALETTE, tooltip: { trigger: 'item', formatter: p => `${p.name}<br/>收入 ${wan(p.value)}<br/>毛利率 ${pct(p.data.gm)}` },
    series: [{ type: 'pie', radius: ['40%', '70%'], label: { formatter: '{b}\n{d}%' },
      data: mix.map(r => ({ name: SERIES[r.series] || r.series, value: r.revenue, gm: r.gross_margin })) }]
  })

  chart(custEl.value).setOption({
    color: PALETTE, tooltip: { trigger: 'axis', valueFormatter: v => wan(v) },
    grid: { left: 180, right: 30, top: 10, bottom: 20 },
    xAxis: { type: 'value', axisLabel: { formatter: v => v / 1e6 + 'M' } },
    yAxis: { type: 'category', inverse: true, data: cust.map(r => r.customer_name), axisLabel: { width: 170, overflow: 'truncate' } },
    series: [{ type: 'bar', data: cust.map(r => r.revenue) }]
  })

  const order = ['未到期', '逾期1-30天', '逾期31-60天', '逾期61-90天', '逾期90天以上']
  chart(agingEl.value).setOption({
    tooltip: { trigger: 'axis', valueFormatter: v => wan(v) }, grid: { left: 60, right: 20, top: 10, bottom: 30 },
    xAxis: { type: 'category', data: order },
    yAxis: { type: 'value', axisLabel: { formatter: v => v / 1e6 + 'M' } },
    series: [{ type: 'bar', data: order.map((b, i) => ({
      value: ag.buckets.find(x => x.bucket === b)?.amount || 0,
      itemStyle: { color: ['#4f9d69', '#e8a33d', '#e0823d', '#d0602c', '#b0302c'][i] }
    })) }]
  })

  const lines = [...new Set(yields.map(r => r.line_code))]
  const ym = [...new Set(yields.map(r => r.month))]
  chart(yieldEl.value).setOption({
    color: PALETTE, tooltip: { trigger: 'axis', valueFormatter: v => pct(v, 2) }, legend: { top: 0 },
    grid: { left: 55, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: ym },
    yAxis: { type: 'value', min: 0.9, max: 1, axisLabel: { formatter: v => (v * 100).toFixed(0) + '%' } },
    series: lines.map(l => ({ name: l, type: 'line', smooth: true, showSymbol: false,
      data: ym.map(mm => yields.find(r => r.month === mm && r.line_code === l)?.yield_rate ?? null) }))
  })

  const cats = [...new Set(exp.map(r => r.category))]
  const depts = [...new Set(exp.map(r => r.dept))]
  chart(expEl.value).setOption({
    color: PALETTE, tooltip: { trigger: 'axis', valueFormatter: v => wan(v) }, legend: { top: 0 },
    grid: { left: 60, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: cats },
    yAxis: { type: 'value', axisLabel: { formatter: v => v / 1e4 + '万' } },
    series: depts.map(dp => ({ name: dp, type: 'bar', stack: 'e',
      data: cats.map(c => exp.find(r => r.category === c && r.dept === dp)?.amount || 0) }))
  })
}

function resize() {
  charts.forEach(c => c.resize())
}

onMounted(async () => {
  month.value = (await http.get('/bi/latest-month')).month
  await loadAll()
  window.addEventListener('resize', resize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  charts.forEach(c => c.dispose())
})
</script>

<style scoped>
.kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; margin-bottom: 12px; }
.kpi { background: #fff; border-radius: 8px; padding: 12px 14px; }
.kpi-label { color: #8a8f99; font-size: 13px; }
.kpi-value { font-size: 22px; font-weight: 600; margin: 4px 0; color: #1f3864; }
.kpi-sub { font-size: 12px; color: #8a8f99; min-height: 16px; }
.kpi-sub.up { color: #3d8c5a; }
.kpi-sub.down { color: #c0392b; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(460px, 1fr)); gap: 12px; }
.panel { background: #fff; border-radius: 8px; padding: 12px 14px; min-width: 0; }
.panel.wide { grid-column: 1 / -1; }
.panel h3 { margin: 0 0 8px; font-size: 14px; font-weight: 600; }
.chart { height: 320px; }
.chart.short { height: 200px; }
@media (max-width: 520px) { .grid { grid-template-columns: 1fr; } }
</style>
