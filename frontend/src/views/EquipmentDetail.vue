<template>
  <div v-if="d">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="设备编码">{{ d.code }}</el-descriptions-item>
      <el-descriptions-item label="名称">{{ d.name }}</el-descriptions-item>
      <el-descriptions-item label="产线">{{ d.line_code || '公用' }}</el-descriptions-item>
      <el-descriptions-item label="关键度">{{ d.criticality }}（停机损失产线产能 {{ pct(d.loss_factor, 0) }}）</el-descriptions-item>
      <el-descriptions-item label="厂商 / 型号">{{ d.vendor }} / {{ d.model }}</el-descriptions-item>
      <el-descriptions-item label="购置">{{ d.purchase_date }}</el-descriptions-item>
      <el-descriptions-item label="近一年故障">{{ d.failures }} 次</el-descriptions-item>
      <el-descriptions-item label="MTBF / MTTR">{{ d.mtbfHours }} h / {{ d.mttrHours }} h</el-descriptions-item>
      <el-descriptions-item label="固有可用度">{{ pct(d.inherentAvailability, 2) }}</el-descriptions-item>
    </el-descriptions>
    <h4>保养计划</h4>
    <el-table :data="d.pmPlans" size="small" border>
      <el-table-column prop="name" label="计划" />
      <el-table-column prop="interval_days" label="周期(天)" width="90" />
      <el-table-column prop="est_minutes" label="停机(分)" width="90" />
      <el-table-column prop="last_done" label="上次" width="100" />
      <el-table-column prop="next_due" label="下次" width="100" />
      <el-table-column prop="checklist" label="保养内容" min-width="220" />
    </el-table>
    <h4>维修 / 保养记录（最近 50 条）</h4>
    <el-table :data="d.workOrders" size="small" border max-height="360">
      <el-table-column prop="wo_no" label="单号" width="130" />
      <el-table-column label="类型" width="70"><template #default="{ row }">{{ row.type === 'PM' ? '保养' : '故障' }}</template></el-table-column>
      <el-table-column label="报修时间" width="140"><template #default="{ row }">{{ fmt(row.reported_at) }}</template></el-table-column>
      <el-table-column prop="fault_desc" label="现象" min-width="130" />
      <el-table-column prop="cause" label="原因" min-width="130" />
      <el-table-column prop="action" label="措施" min-width="150" />
      <el-table-column prop="downtime_min" label="停机(分)" width="80" align="right" />
      <el-table-column label="备件" width="90" align="right"><template #default="{ row }">{{ Number(row.spare_cost) ? money(row.spare_cost) : '' }}</template></el-table-column>
      <el-table-column prop="technician" label="技术员" width="80" />
    </el-table>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import http from '../api/http'
import { money, pct } from '../api/format'

const props = defineProps({ id: Number })
const d = ref(null)
const fmt = v => v ? String(v).replace('T', ' ').slice(0, 16) : ''
onMounted(async () => { d.value = await http.get(`/eam/equipment/${props.id}/reliability`, { params: { to: '2026-09-28' } }) })
</script>

<style scoped>
h4 { margin: 16px 0 8px; }
</style>
