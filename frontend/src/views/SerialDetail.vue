<template>
  <div v-if="d">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="序列号">{{ d.sn }}</el-descriptions-item>
      <el-descriptions-item label="产品">{{ d.product_code }}</el-descriptions-item>
      <el-descriptions-item label="测试结果"><el-tag size="small" :type="d.result === 'PASS' ? 'success' : 'danger'">{{ d.result }}</el-tag> {{ d.fail_item || '' }}</el-descriptions-item>
      <el-descriptions-item label="生产工单（成品批次）">{{ d.wo_no }}</el-descriptions-item>
      <el-descriptions-item label="生产日期">{{ d.produced_date }}</el-descriptions-item>
      <el-descriptions-item label="出货">{{ d.delivery_no || '未出货' }} {{ d.customer_name || '' }}</el-descriptions-item>
      <el-descriptions-item label="反电势">{{ d.back_emf }} V/krpm</el-descriptions-item>
      <el-descriptions-item label="空载电流">{{ d.no_load_current }} A</el-descriptions-item>
      <el-descriptions-item label="编码器零位">{{ d.encoder_offset }}°</el-descriptions-item>
      <el-descriptions-item label="绝缘电阻">{{ d.insulation_mohm }} MΩ</el-descriptions-item>
      <el-descriptions-item label="振动">{{ d.vibration }} mm/s</el-descriptions-item>
    </el-descriptions>
    <div class="actions"><el-button @click="trace">按工单批次追溯物料（转子 / 铁芯 / 磁钢 / 编码器批次）</el-button></div>
    <h4>本工单过程检验</h4>
    <el-table :data="d.ipqc" size="small" border>
      <el-table-column prop="type" label="类型" width="80" />
      <el-table-column label="时间" width="150"><template #default="{ row }">{{ String(row.inspect_time).replace('T', ' ').slice(0, 16) }}</template></el-table-column>
      <el-table-column prop="inspector" label="检验员" width="90" />
      <el-table-column prop="result" label="结果" width="70" />
      <el-table-column prop="remark" label="备注" />
    </el-table>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import http from '../api/http'

const props = defineProps({ id: Number })
const d = ref(null)
onMounted(async () => { d.value = await http.get(`/mes/serials/by-id/${props.id}`) })
function trace() { if (d.value.delivery_no) window.location.href = `/quality/trace?mode=delivery&key=${d.value.delivery_no}` }
</script>

<style scoped>
.actions { margin: 12px 0; }
h4 { margin: 16px 0 8px; }
</style>
