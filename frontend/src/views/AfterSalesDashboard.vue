<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" :clearable="false" @change="load" />
        <el-input v-model="sn" placeholder="序列号查保修，如 SV2511…" style="width: 220px" clearable @keyup.enter="lookup" />
        <el-button @click="lookup">查询</el-button>
        <span class="muted">P1 客户停线 7×24 响应 2h / 解决 24h；P2、P3 按工作时间计。伺服保修 18 个月，其他 12 个月</span>
      </div>
      <div v-if="d" class="kpis">
        <div class="kpi"><div class="v">{{ d.summary.tickets }}</div><div class="l">服务工单（故障 {{ d.summary.faults }}，P1 {{ d.summary.p1 }}）</div></div>
        <div class="kpi"><div class="v" :style="{ color: d.summary.sla_rate < 0.9 ? '#c0392b' : '#3d8c5a' }">{{ pct(d.summary.sla_rate) }}</div><div class="l">SLA 达成率</div></div>
        <div class="kpi"><div class="v">{{ d.summary.avg_response_h }} / {{ d.summary.avg_resolve_h }} h</div><div class="l">平均响应 / 解决</div></div>
        <div class="kpi"><div class="v">{{ pct(d.summary.first_fix_rate) }}</div><div class="l">故障一次修复率（未返厂）</div></div>
        <div class="kpi"><div class="v">{{ d.summary.csat }}</div><div class="l">客户满意度（5 分制）</div></div>
        <div class="kpi"><div class="v">{{ d.rma.rmas }} 单 / {{ qty(d.rma.qty) }} 台</div><div class="l">RMA（换新 {{ d.rma.replaced }}，维修 {{ d.rma.repaired }}，无故障 {{ d.rma.no_fault }}）</div></div>
        <div class="kpi"><div class="v">{{ wan(Number(d.rma.cost) + Number(d.rma.claims)) }}</div><div class="l">保内成本（含客户索赔 {{ wan(d.rma.claims) }}）</div></div>
        <div class="kpi"><div class="v">{{ wan(d.provisionBalance) }}</div><div class="l">质量保证金余额（计提 {{ pct(d.provisionRate, 2) }}）</div></div>
      </div>
      <div class="grid">
        <div ref="trendEl" class="chart" />
        <div ref="failEl" class="chart" />
      </div>
      <div class="grid">
        <div>
          <h4>故障类型</h4>
          <el-table :data="d?.faults || []" size="small" border>
            <el-table-column prop="fault_code" label="故障" width="110"><template #default="{ row }">{{ FAULT[row.fault_code] || row.fault_code }}</template></el-table-column>
            <el-table-column prop="n" label="工单数" align="right" /><el-table-column prop="servo" label="其中伺服" align="right" />
          </el-table>
        </div>
        <div>
          <h4>服务工程师</h4>
          <el-table :data="d?.engineers || []" size="small" border>
            <el-table-column prop="name" label="工程师" /><el-table-column prop="tickets" label="工单" align="right" />
            <el-table-column prop="onsite" label="上门" align="right" /><el-table-column prop="breached" label="超时" align="right" />
            <el-table-column prop="csat" label="满意度" align="right" />
          </el-table>
        </div>
      </div>
    </div>
    <el-dialog v-model="show" title="保修查询" width="640px">
      <el-descriptions v-if="w" :column="2" border size="small">
        <el-descriptions-item label="序列号">{{ w.sn }}</el-descriptions-item><el-descriptions-item label="产品">{{ w.product_code }} {{ w.product_name }}</el-descriptions-item>
        <el-descriptions-item label="生产工单">{{ w.wo_no }}（{{ w.produced_date }}）</el-descriptions-item><el-descriptions-item label="出厂测试">{{ w.result }}</el-descriptions-item>
        <el-descriptions-item label="客户">{{ w.customer_name || '未发货' }}</el-descriptions-item><el-descriptions-item label="发货">{{ w.delivery_no }} {{ w.delivery_date }}</el-descriptions-item>
        <el-descriptions-item label="保修期">{{ w.warrantyMonths }} 个月，至 {{ w.warrantyEnd || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态"><el-tag :type="w.inWarranty ? 'success' : 'info'">{{ w.inWarranty ? '保内' : '保外' }}</el-tag></el-descriptions-item>
      </el-descriptions>
      <el-table v-if="w" :data="w.tickets" size="small" border style="margin-top: 10px">
        <el-table-column prop="ticket_no" label="工单" width="150" /><el-table-column prop="created_at" label="时间" width="160" />
        <el-table-column prop="fault_code" label="故障" /><el-table-column prop="resolution" label="处理" /><el-table-column prop="status" label="状态" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'
import { pct, qty, wan } from '../api/format'

const FAULT = { NOISE: '异响振动', ENCODER: '编码器故障', OVERHEAT: '过热', NO_START: '不启动', SPEED: '速度异常', PARAM: '参数设置', OTHER: '其他' }
const range = ref(['2025-10-01', '2026-09-28'])
const d = ref(null), w = ref(null), sn = ref(''), show = ref(false)
const trendEl = ref(), failEl = ref()
let charts = []
async function load() {
  d.value = await http.get('/aftersales/dashboard', { params: { from: range.value[0], to: range.value[1] } })
  await nextTick()
  charts.forEach(c => c.dispose())
  charts = [echarts.init(trendEl.value), echarts.init(failEl.value)]
  const m = d.value.monthly
  charts[0].setOption({ title: { text: '工单与质保金', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 50, right: 60, bottom: 30 }, xAxis: { data: m.map(x => x.period) },
    yAxis: [{ type: 'value', name: '单' }, { type: 'value', name: '万元' }],
    series: [{ name: '工单', type: 'bar', data: m.map(x => x.tickets) }, { name: '超时', type: 'bar', data: m.map(x => x.breached) },
      { name: '计提', type: 'line', yAxisIndex: 1, data: m.map(x => (x.accrual / 1e4).toFixed(1)) },
      { name: '索赔耗用', type: 'line', yAxisIndex: 1, data: m.map(x => (x.claims / 1e4).toFixed(1)) }] })
  const f = d.value.servoFieldFailure.filter(x => x.shipped >= 200)
  charts[1].setOption({ title: { text: '伺服现场故障率（按生产月，每千台）', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' },
    grid: { top: 40, left: 45, right: 20, bottom: 30 }, xAxis: { data: f.map(x => x.build_month) }, yAxis: { type: 'value' },
    series: [{ type: 'bar', data: f.map(x => ({ value: x.per_thousand, itemStyle: { color: x.build_month >= '2025-11' && x.build_month <= '2026-01' ? '#c0392b' : '#5470c6' } })) }] })
}
async function lookup() {
  if (!sn.value) return
  w.value = await http.get(`/aftersales/warranty/${encodeURIComponent(sn.value.trim())}`)
  show.value = true
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
