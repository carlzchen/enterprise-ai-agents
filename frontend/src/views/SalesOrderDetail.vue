<template>
  <div v-if="d" class="detail">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="订单号">{{ d.order_no }}</el-descriptions-item>
      <el-descriptions-item label="客户">{{ d.customer_name }}</el-descriptions-item>
      <el-descriptions-item label="状态"><el-tag size="small" :type="STATUS[d.status]?.[1]">{{ STATUS[d.status]?.[0] }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="下单日期">{{ d.order_date }}</el-descriptions-item>
      <el-descriptions-item label="要求交期">{{ d.required_date }}</el-descriptions-item>
      <el-descriptions-item label="销售员">{{ d.salesperson }}</el-descriptions-item>
      <el-descriptions-item label="币种/汇率">{{ d.currency }} / {{ d.fx_rate }}</el-descriptions-item>
      <el-descriptions-item label="金额(原币)">{{ money(d.amount) }}</el-descriptions-item>
      <el-descriptions-item label="金额(人民币)">{{ money(d.amount_cny) }}</el-descriptions-item>
    </el-descriptions>

    <div class="actions">
      <el-button v-if="d.status === 'DRAFT' && hasRole(['ADMIN','SALES'])" type="primary" @click="act('confirm')">确认订单（信用检查）</el-button>
      <el-button v-if="['DRAFT','CONFIRMED'].includes(d.status) && hasRole(['ADMIN','SALES'])" @click="act('cancel')">取消订单</el-button>
      <el-button v-if="['CONFIRMED','PARTIAL'].includes(d.status) && hasRole(['ADMIN','WAREHOUSE','SALES'])" type="success" @click="shipAll">按未发数量发货</el-button>
    </div>

    <h4>订单行</h4>
    <el-table :data="d.lines" size="small" border>
      <el-table-column prop="line_no" label="行" width="50" />
      <el-table-column prop="product_code" label="产品编码" min-width="150" />
      <el-table-column prop="product_name" label="产品名称" min-width="180" />
      <el-table-column label="数量" align="right"><template #default="{ row }">{{ qty(row.qty) }}</template></el-table-column>
      <el-table-column label="单价" align="right"><template #default="{ row }">{{ row.unit_price }}</template></el-table-column>
      <el-table-column label="已发" align="right"><template #default="{ row }">{{ qty(row.shipped_qty) }}</template></el-table-column>
      <el-table-column label="金额" align="right"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
    </el-table>

    <h4>发货记录</h4>
    <el-table :data="d.deliveries" size="small" border>
      <el-table-column prop="delivery_no" label="发货单号" />
      <el-table-column prop="delivery_date" label="发货日期" />
      <el-table-column label="金额(人民币)" align="right"><template #default="{ row }">{{ money(row.amount_cny) }}</template></el-table-column>
      <el-table-column label="状态"><template #default="{ row }">{{ STATUS[row.status]?.[0] }}</template></el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button v-if="row.status === 'SHIPPED' && hasRole(['ADMIN','FINANCE'])" link type="primary" @click="invoice(row)">开票</el-button>
        </template>
      </el-table-column>
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
  d.value = await http.get(`/sales/orders/${props.id}`)
}

async function done(msg) {
  ElMessage.success(msg)
  await load()
  emit('changed')
}

async function act(action) {
  await http.post(`/sales/orders/${props.id}/${action}`)
  await done('操作成功')
}

async function shipAll() {
  const lines = d.value.lines.filter(l => Number(l.open_qty) > 0).map(l => ({ orderLineId: l.id, qty: l.open_qty }))
  await http.post(`/sales/orders/${props.id}/ship`, { date: today(), lines })
  await done('已发货，库存已扣减')
}

async function invoice(row) {
  await http.post(`/sales/deliveries/${row.id}/invoice`, { date: today() })
  await done('已开票，应收已生成')
}

onMounted(load)
</script>

<style scoped>
.actions { margin: 12px 0; display: flex; gap: 8px; }
h4 { margin: 16px 0 8px; }
</style>
