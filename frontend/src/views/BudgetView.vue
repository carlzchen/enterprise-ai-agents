<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-select v-model="year" style="width: 100px" @change="load"><el-option v-for="y in [2024, 2025, 2026]" :key="y" :label="y" :value="y" /></el-select>
        <span class="muted">报销按月、部门、费用类别检查预算：执行后超出预算的，报销流程增加总经理审批</span>
      </div>
      <div ref="chartEl" class="chart" />
      <el-table :data="r?.rows || []" size="small" border>
        <el-table-column prop="dept" label="部门" width="100" /><el-table-column prop="category" label="类别" width="110" />
        <el-table-column label="预算" align="right"><template #default="{ row }">{{ money(row.budget) }}</template></el-table-column>
        <el-table-column label="实际" align="right"><template #default="{ row }">{{ money(row.actual) }}</template></el-table-column>
        <el-table-column label="执行率" width="180"><template #default="{ row }">
          <el-progress :percentage="Math.min(150, Math.round(row.rate * 100))" :status="row.rate > 1 ? 'exception' : row.rate > 0.9 ? 'warning' : 'success'" :format="() => pct(row.rate)" />
        </template></el-table-column>
        <el-table-column prop="over_months" label="超预算月数" width="100" align="right" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import http from '../api/http'
import { money, pct } from '../api/format'

const year = ref(2026)
const r = ref(null)
const chartEl = ref()
let chart
async function load() {
  r.value = await http.get('/treasury/budget', { params: { year: year.value } })
  await nextTick()
  chart?.dispose()
  chart = echarts.init(chartEl.value)
  const m = r.value.monthly
  chart.setOption({ title: { text: '月度预算 vs 实际', textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 22 },
    grid: { top: 60, left: 70, right: 20, bottom: 30 }, xAxis: { data: m.map(x => x.month + '月') }, yAxis: { axisLabel: { formatter: v => (v / 1e4).toFixed(0) + '万' } },
    series: [{ name: '预算', type: 'bar', data: m.map(x => x.budget) }, { name: '实际', type: 'bar', data: m.map(x => x.actual) }] })
}
onMounted(load)
onBeforeUnmount(() => chart?.dispose())
</script>

<style scoped>
.chart { height: 280px; margin-bottom: 12px; }
</style>
