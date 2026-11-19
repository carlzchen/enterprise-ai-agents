<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-button v-if="hasRole(['ADMIN','PRODUCTION'])" type="primary" :loading="running" @click="run">重新排产</el-button>
        <span v-if="plan.plan_no" class="muted">方案 {{ plan.plan_no }}｜基准日 {{ plan.plan_date }}｜工单 {{ plan.order_count }} 个｜
          <b :style="{ color: plan.late_count ? '#c0392b' : '#3d8c5a' }">预计延期 {{ plan.late_count }} 个</b></span>
        <span class="muted">算法：按 BOM 低阶码从底层组件逐层排产；齐套顺序 现存量 → 替代料 → 在途采购 → 下层组件计划完工；有限产能 + 交期最早优先（EDD），缺料工单让位，跳过周末</span>
      </div>
      <div ref="ganttEl" class="gantt" />
      <div ref="loadEl" class="load" />
      <h4>延期、缺料与替代料预警</h4>
      <el-table :data="late" size="small" border max-height="260" empty-text="没有延期或缺料的工单">
        <el-table-column prop="wo_no" label="工单" />
        <el-table-column label="层级" width="70"><template #default="{ row }">
          <el-tag size="small" :type="row.bom_level ? 'warning' : ''">{{ row.bom_level ? `组件 L${row.bom_level}` : '成品' }}</el-tag>
        </template></el-table-column>
        <el-table-column prop="product_code" label="产品 / 组件" min-width="160" />
        <el-table-column prop="parent_wo_no" label="上级工单" min-width="120" />
        <el-table-column prop="line_code" label="产线" width="70" />
        <el-table-column prop="qty" label="待产数量" align="right" />
        <el-table-column prop="start_date" label="计划开工" />
        <el-table-column prop="end_date" label="计划完工" />
        <el-table-column prop="due_date" label="需求交期" />
        <el-table-column prop="late_days" label="延期(天)" align="right" />
        <el-table-column label="齐套" width="100"><template #default="{ row }">
          <el-tag size="small" :type="{ READY: 'success', WAIT_PO: 'warning', SHORTAGE: 'danger' }[row.kit_status]">{{ { READY: '齐套', WAIT_PO: '等待到货', SHORTAGE: '缺料' }[row.kit_status] }}</el-tag>
        </template></el-table-column>
        <el-table-column prop="earliest_start" label="最早齐套" width="100" />
        <el-table-column label="缺料 / 替代料" min-width="240"><template #default="{ row }">
          <div v-if="row.shortage">{{ row.shortage }}</div><div v-if="row.substitution" style="color: #3d8c5a">{{ row.substitution }}</div>
        </template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'

const plan = ref({})
const running = ref(false)
const ganttEl = ref(), loadEl = ref()
let charts = []
const late = computed(() => (plan.value.items || []).filter(i => i.late_days > 0 || i.kit_status !== 'READY' || i.substitution))
const day = s => new Date(s + 'T00:00:00').getTime()

async function load() {
  plan.value = await http.get('/aps/latest')
  await nextTick()
  draw()
}

async function run() {
  running.value = true
  try {
    plan.value = await http.post('/aps/run')
    ElMessage.success('排产完成')
    await nextTick()
    draw()
  } finally {
    running.value = false
  }
}

function draw() {
  charts.forEach(c => c.dispose())
  charts = []
  const items = plan.value.items || []
  if (!items.length) return
  const lines = [...new Set(items.map(i => i.line_code))]
  const g = echarts.init(ganttEl.value)
  charts.push(g)
  g.setOption({
    title: { text: '工单排程甘特图', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: { formatter: p => `${p.data.wo}<br/>${p.data.product}<br/>${p.data.start} ~ ${p.data.end}<br/>交期 ${p.data.due}${p.data.late ? `<br/><b style="color:#c0392b">延期 ${p.data.late} 天</b>` : ''}` },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: { type: 'time' },
    yAxis: { type: 'category', data: lines, inverse: true },
    dataZoom: [{ type: 'slider', xAxisIndex: 0, height: 16, bottom: 8 }],
    series: [{
      type: 'custom',
      renderItem: (params, api) => {
        const y = api.value(0), s = api.coord([api.value(1), y]), e = api.coord([api.value(2) + 864e5, y])
        const h = api.size([0, 1])[1] * 0.6
        return { type: 'rect', shape: { x: s[0], y: s[1] - h / 2, width: Math.max(2, e[0] - s[0] - 1), height: h },
          style: { fill: api.value(3) > 0 ? '#d0602c' : ['#3a6ea5', '#4f9d69', '#8a6fb5'][params.dataIndex % 3], stroke: '#fff' } }
      },
      encode: { x: [1, 2], y: 0 },
      data: items.map(i => ({ value: [lines.indexOf(i.line_code), day(i.start_date), day(i.end_date), i.late_days],
        wo: i.wo_no, product: i.product_code, start: i.start_date, end: i.end_date, due: i.due_date, late: i.late_days }))
    }]
  })
  const cap = Object.fromEntries((plan.value.lines || []).map(l => [l.line_code, l.daily_capacity]))
  const loadMap = {}
  for (const i of items) {
    // 按工单的开工~完工区间平均摊到每个工作日，用于展示负荷率
    const days = []
    for (let t = day(i.start_date); t <= day(i.end_date); t += 864e5) { const w = new Date(t).getDay(); if (w && w !== 6) days.push(t) }
    for (const t of days) { const k = i.line_code; (loadMap[k] ||= {})[t] = ((loadMap[k] ||= {})[t] || 0) + Number(i.qty) / days.length }
  }
  const l = echarts.init(loadEl.value)
  charts.push(l)
  l.setOption({
    title: { text: '产线负荷率（排产量 / 日产能）', left: 'center', textStyle: { fontSize: 14 } },
    tooltip: { trigger: 'axis', valueFormatter: v => (v * 100).toFixed(0) + '%' }, legend: { bottom: 0 },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: { type: 'time' }, yAxis: { type: 'value', axisLabel: { formatter: v => (v * 100).toFixed(0) + '%' } },
    series: Object.entries(loadMap).map(([k, m]) => ({ name: k, type: 'line', step: 'middle', showSymbol: false,
      data: Object.entries(m).sort((a, b) => a[0] - b[0]).map(([t, q]) => [Number(t), Math.min(q / cap[k], 1.2)]) }))
  })
}

const resize = () => charts.forEach(c => c.resize())
onMounted(() => { load(); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); charts.forEach(c => c.dispose()) })
</script>

<style scoped>
.gantt { height: 320px; }
.load { height: 240px; margin-top: 8px; }
h4 { margin: 12px 0 8px; }
</style>
