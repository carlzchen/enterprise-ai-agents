<template>
  <div v-if="d">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="调拨单号">{{ d.transfer_no }}</el-descriptions-item>
      <el-descriptions-item label="调出仓">{{ d.from_warehouse }}</el-descriptions-item>
      <el-descriptions-item label="调入仓">{{ d.to_warehouse }}</el-descriptions-item>
      <el-descriptions-item label="状态"><el-tag size="small" :type="STATUS[d.status]?.[1]">{{ STATUS[d.status]?.[0] }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="发出日期">{{ d.ship_date || '-' }}</el-descriptions-item>
      <el-descriptions-item label="预计 / 实际到货">{{ d.expected_date || '-' }} / {{ d.receive_date || '-' }}</el-descriptions-item>
      <el-descriptions-item label="在途金额">{{ money(d.amount) }}</el-descriptions-item>
      <el-descriptions-item label="创建人">{{ d.created_by }}</el-descriptions-item>
      <el-descriptions-item label="备注">{{ d.remark }}</el-descriptions-item>
    </el-descriptions>
    <div class="actions" v-if="hasRole(['ADMIN','WAREHOUSE'])">
      <el-button v-if="d.status === 'CREATED'" type="primary" @click="act('ship', '已发出，货物转入调拨在途')">发出</el-button>
      <el-button v-if="d.status === 'SHIPPED'" type="success" @click="act('receive', '已到货入库，批次随货转入调入仓')">确认到货</el-button>
      <el-button v-if="d.status === 'CREATED'" @click="act('cancel', '已取消')">取消</el-button>
    </div>
    <el-table :data="d.lines" size="small" border>
      <el-table-column prop="item_code" label="编码" min-width="150" />
      <el-table-column prop="item_name" label="名称" min-width="180" />
      <el-table-column label="数量" align="right"><template #default="{ row }">{{ qty(row.qty) }}</template></el-table-column>
      <el-table-column label="单位成本" align="right"><template #default="{ row }">{{ money(row.unit_cost) }}</template></el-table-column>
      <el-table-column label="金额" align="right"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
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
  d.value = await http.get(`/transfers/${props.id}`)
}

async function act(action, msg) {
  await http.post(`/transfers/${props.id}/${action}`, { date: today() })
  ElMessage.success(msg)
  await load()
  emit('changed')
}

onMounted(load)
</script>

<style scoped>
.actions { margin: 12px 0; }
</style>
