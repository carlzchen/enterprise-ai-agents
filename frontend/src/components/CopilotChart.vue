<template>
  <div class="cchart">
    <el-radio-group v-model="kind" size="small" class="switch">
      <el-radio-button v-for="k in kinds" :key="k.value" :value="k.value">{{ k.label }}</el-radio-button>
    </el-radio-group>
    <div ref="el" class="canvas" />
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

/** spec 由后端给出：{type, title, x, series: [{name, type, pct, axis, data}]}；这里只负责换成 ECharts 配置 */
const props = defineProps({ spec: { type: Object, required: true } })
const el = ref()
const kind = ref(props.spec.type)
const kinds = [{ value: 'bar', label: '柱状' }, { value: 'line', label: '折线' }, { value: 'pie', label: '饼图' }]
let chart

const pctFmt = v => (v * 100).toFixed(1) + '%'
const numFmt = v => (Math.abs(v) >= 1e8 ? (v / 1e8).toFixed(2) + '亿' : Math.abs(v) >= 1e4 ? (v / 1e4).toFixed(1) + '万' : v)

function option() {
  const s = props.spec
  const title = { text: s.title, textStyle: { fontSize: 13, fontWeight: 600 }, left: 0 }
  if (kind.value === 'pie') {
    const first = s.series.find(x => !x.pct) || s.series[0]
    return { title, tooltip: { trigger: 'item', formatter: '{b}：{c}（{d}%）' }, legend: { type: 'scroll', bottom: 0 },
      series: [{ type: 'pie', radius: ['35%', '62%'], center: ['50%', '50%'], data: s.x.map((n, i) => ({ name: n, value: first.data[i] })) }] }
  }
  const multi = s.series.length > 2 || !s.series.some(x => x.axis === 1)
  const axisOf = i => (multi ? 0 : s.series[i].axis || 0)
  const yAxis = [0, 1].filter(a => a === 0 || s.series.some((x, i) => axisOf(i) === 1)).map(a => {
    const pct = s.series.find((x, i) => axisOf(i) === a)?.pct
    return { type: 'value', scale: pct, axisLabel: { formatter: pct ? pctFmt : numFmt }, splitLine: { show: a === 0 } }
  })
  return {
    title, legend: { top: 22, type: 'scroll' }, grid: { top: 58, left: 56, right: yAxis.length > 1 ? 56 : 20, bottom: 40 },
    tooltip: { trigger: 'axis', valueFormatter: undefined,
      formatter: ps => ps[0].axisValueLabel + '<br>' + ps.map(p => `${p.marker}${p.seriesName}：${s.series[p.seriesIndex].pct ? pctFmt(p.value ?? 0) : Number(p.value ?? 0).toLocaleString()}`).join('<br>') },
    xAxis: { type: 'category', data: s.x, axisLabel: { interval: s.x.length > 14 ? 'auto' : 0, rotate: s.x.length > 8 ? 30 : 0 } },
    yAxis,
    series: s.series.map((x, i) => ({ name: x.name, data: x.data, yAxisIndex: axisOf(i), smooth: true,
      type: s.series.length > 2 ? kind.value : (i === 0 ? kind.value : (x.type === 'line' || kind.value === 'bar' ? 'line' : 'bar')) }))
  }
}

function draw() {
  if (!el.value) return
  chart = chart || echarts.init(el.value)
  chart.setOption(option(), true)
}
const resize = () => chart?.resize()

onMounted(() => { nextTick(draw); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); chart?.dispose() })
watch(kind, draw)
watch(() => props.spec, s => { kind.value = s.type; draw() })
</script>

<style scoped>
.cchart { margin-top: 12px; position: relative; }
.switch { position: absolute; right: 0; top: -2px; z-index: 2; }
.canvas { width: 100%; height: 300px; }
</style>
