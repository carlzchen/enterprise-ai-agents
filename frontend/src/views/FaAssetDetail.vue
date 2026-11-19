<template>
  <div v-if="d">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="资产编号">{{ d.asset_no }}</el-descriptions-item>
      <el-descriptions-item label="名称">{{ d.name }}</el-descriptions-item>
      <el-descriptions-item label="类别">{{ d.category_name }}（{{ d.method === 'DDB' ? '双倍余额递减' : '年限平均' }}，{{ d.useful_life_months }} 个月，残值率 {{ pct(d.residual_rate, 0) }}）</el-descriptions-item>
      <el-descriptions-item label="使用部门">{{ d.dept_name }}（{{ COST[d.cost_type] }}）</el-descriptions-item>
      <el-descriptions-item label="投用日期">{{ d.in_service_date }}</el-descriptions-item>
      <el-descriptions-item label="关联设备">{{ d.equipment_code || '-' }}</el-descriptions-item>
      <el-descriptions-item label="原值">{{ money(d.original_cost) }}</el-descriptions-item>
      <el-descriptions-item label="累计折旧">{{ money(d.accumulated_dep) }}（{{ pct(d.dep_progress) }}）</el-descriptions-item>
      <el-descriptions-item label="净值">{{ money(d.net_value) }}</el-descriptions-item>
      <el-descriptions-item label="状态">{{ d.status === 'IN_USE' ? '在用' : '已处置 ' + d.disposal_date }}</el-descriptions-item>
      <el-descriptions-item label="最近计提">{{ d.last_dep_period }}</el-descriptions-item>
    </el-descriptions>
    <div v-if="d.status === 'IN_USE' && hasRole(['ADMIN','FINANCE'])" class="actions"><el-button type="danger" plain @click="dispose">处置</el-button></div>
    <h4>折旧计划（年末累计）</h4>
    <div ref="chartEl" style="height: 220px" />
    <h4>最近折旧</h4>
    <el-table :data="d.depreciation" size="small" border max-height="240">
      <el-table-column prop="period" label="期间" width="90" /><el-table-column label="金额" align="right"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
      <el-table-column label="累计" align="right"><template #default="{ row }">{{ money(row.accumulated_after) }}</template></el-table-column>
      <el-table-column prop="expense_account" label="费用科目" width="90" />
    </el-table>
    <h4>变动记录</h4>
    <el-table :data="d.changes" size="small" border>
      <el-table-column prop="change_date" label="日期" width="100" /><el-table-column prop="change_type" label="类型" width="90" />
      <el-table-column label="金额" align="right" width="130"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column><el-table-column prop="remark" label="说明" />
    </el-table>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { money, pct } from '../api/format'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const d = ref(null)
const chartEl = ref()
let chart
const COST = { PRODUCTION: '计入制造费用', SALES: '计入销售费用', ADMIN: '计入管理费用', RND: '计入研发费用' }
async function load() {
  d.value = await http.get(`/fa/assets/${props.id}`)
  await nextTick()
  chart?.dispose()
  chart = echarts.init(chartEl.value)
  chart.setOption({ tooltip: { trigger: 'axis' }, grid: { top: 20, left: 70, right: 20, bottom: 30 }, xAxis: { data: d.value.schedule.map(x => x.period.slice(0, 4)) },
    yAxis: { axisLabel: { formatter: v => (v / 1e4).toFixed(0) + '万' } }, series: [{ type: 'bar', data: d.value.schedule.map(x => x.accumulated) }] })
}
async function dispose() {
  const { value } = await ElMessageBox.prompt('处置收入（元）', `处置 ${d.value.name}（当月照提折旧，差额计入资产处置收益）`, { inputValue: '0', inputPattern: /^\d+(\.\d+)?$/ })
  const r = await http.post(`/fa/assets/${props.id}/dispose`, { proceeds: Number(value), clearingCost: 0, reason: '报废 / 出售' })
  ElMessage.success(`已处置，处置损益 ${money(r.gain)}`)
  await load(); emit('changed')
}
onMounted(load)
onBeforeUnmount(() => chart?.dispose())
</script>

<style scoped>
.actions { margin: 12px 0; }
h4 { margin: 16px 0 8px; }
</style>
