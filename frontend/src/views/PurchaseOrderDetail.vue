<template>
  <div v-if="d">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="采购单号">{{ d.po_no }}</el-descriptions-item>
      <el-descriptions-item label="供应商">{{ d.supplier_name }}</el-descriptions-item>
      <el-descriptions-item label="状态"><el-tag size="small" :type="STATUS[d.status]?.[1]">{{ STATUS[d.status]?.[0] }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="下单日期">{{ d.order_date }}</el-descriptions-item>
      <el-descriptions-item label="预计到货">{{ d.expected_date }}</el-descriptions-item>
      <el-descriptions-item label="金额(不含税)">{{ money(d.amount) }}</el-descriptions-item>
    </el-descriptions>
    <div class="actions">
      <el-button v-if="d.status === 'DRAFT' && hasRole(['ADMIN','PURCHASE'])" type="primary" @click="act('confirm')">确认下单</el-button>
      <el-button v-if="['DRAFT','CONFIRMED'].includes(d.status) && hasRole(['ADMIN','PURCHASE'])" @click="act('cancel')">取消</el-button>
      <el-button v-if="['CONFIRMED','PARTIAL'].includes(d.status) && hasRole(['ADMIN','WAREHOUSE','PURCHASE'])" type="success" @click="receiveAll">登记到货（待检）</el-button>
    </div>
    <h4>采购行</h4>
    <el-table :data="d.lines" size="small" border>
      <el-table-column prop="line_no" label="行" width="50" />
      <el-table-column prop="material_code" label="物料编码" />
      <el-table-column prop="material_name" label="物料名称" min-width="160" />
      <el-table-column label="数量" align="right"><template #default="{ row }">{{ qty(row.qty) }} {{ row.uom }}</template></el-table-column>
      <el-table-column prop="unit_price" label="单价" align="right" />
      <el-table-column label="已到" align="right"><template #default="{ row }">{{ qty(row.received_qty) }}</template></el-table-column>
    </el-table>
    <h4>到货记录</h4>
    <el-table :data="d.receipts" size="small" border>
      <el-table-column prop="gr_no" label="到货单号" />
      <el-table-column prop="receipt_date" label="到货日期" />
      <el-table-column label="状态"><template #default="{ row }">{{ STATUS[row.status]?.[0] }}</template></el-table-column>
      <el-table-column label="合格金额" align="right"><template #default="{ row }">{{ money(row.accepted_amount) }}</template></el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { STATUS, money, qty, today } from '../api/format'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const d = ref(null)

async function load() {
  d.value = await http.get(`/purchase/orders/${props.id}`)
}

async function act(action) {
  await http.post(`/purchase/orders/${props.id}/${action}`)
  ElMessage.success('操作成功')
  await load()
  emit('changed')
}

async function receiveAll() {
  const lines = d.value.lines.filter(l => Number(l.open_qty) > 0).map(l => ({ poLineId: l.id, qty: l.open_qty }))
  await http.post(`/purchase/orders/${props.id}/receive`, { date: today(), lines })
  ElMessage.success('已登记到货，请质量部进行来料检验')
  await load()
  emit('changed')
}

onMounted(load)
</script>

<style scoped>
.actions { margin: 12px 0; display: flex; gap: 8px; }
h4 { margin: 16px 0 8px; }
</style>
