<template>
  <div v-if="d">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="到货单号">{{ d.gr_no }}</el-descriptions-item>
      <el-descriptions-item label="采购单号">{{ d.po_no }}</el-descriptions-item>
      <el-descriptions-item label="供应商">{{ d.supplier_name }}</el-descriptions-item>
      <el-descriptions-item label="到货日期">{{ d.receipt_date }}</el-descriptions-item>
      <el-descriptions-item label="状态"><el-tag size="small" :type="STATUS[d.status]?.[1]">{{ STATUS[d.status]?.[0] }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="合格金额">{{ money(d.accepted_amount) }}</el-descriptions-item>
    </el-descriptions>
    <div class="actions">
      <el-button v-if="d.status === 'COMPLETED' && hasRole(['ADMIN','FINANCE'])" type="primary" @click="invoice">收到发票（生成应付）</el-button>
    </div>
    <el-table :data="d.lines" size="small" border>
      <el-table-column prop="material_code" label="物料编码" />
      <el-table-column prop="material_name" label="物料名称" min-width="160" />
      <el-table-column label="到货数量" align="right"><template #default="{ row }">{{ qty(row.qty) }} {{ row.uom }}</template></el-table-column>
      <el-table-column label="合格入库" align="right"><template #default="{ row }">{{ qty(row.accepted_qty) }}</template></el-table-column>
      <el-table-column label="判退" align="right"><template #default="{ row }">{{ qty(row.rejected_qty) }}</template></el-table-column>
      <el-table-column label="订单价" align="right"><template #default="{ row }">{{ money(row.unit_price) }}</template></el-table-column>
      <el-table-column label="IQC" width="120">
        <template #default="{ row }">
          <el-button v-if="!row.inspected && hasRole(['ADMIN','QUALITY'])" link type="primary" @click="inspect(row)">来料检验</el-button>
          <span v-else-if="row.inspected">已检</span>
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
import { openForm } from '../api/shared'
import { STATUS, money, qty, today } from '../api/format'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const d = ref(null)

async function load() {
  d.value = await http.get(`/purchase/receipts/${props.id}`)
}

function inspect(row) {
  openForm({
    title: `来料检验 - ${row.material_name}`,
    fields: [
      { prop: 'sampleQty', label: '抽样数', type: 'number', precision: 0, required: true },
      { prop: 'defectQty', label: '不良数', type: 'number', precision: 0, required: true },
      { prop: 'defectDesc', label: '不良描述' }
    ],
    initial: { sampleQty: 80, defectQty: 0 },
    onSubmit: async m => {
      const r = await http.post('/quality/iqc', { grLineId: row.id, ...m })
      ElMessage.success(r.result === 'PASS' ? '合格，已入库' : '不合格（不良率超过 AQL 2%），整批判退')
      await load()
      emit('changed')
    }
  })
}

/** 收票：逐行录入发票单价（默认订单价），与暂估价比对，超过 3% 容差的发票进入财务审批 */
function invoice() {
  const lines = d.value.lines.filter(l => Number(l.accepted_qty) > 0).map(l => ({
    id: l.id, material: `${l.material_code} ${l.material_name}`, qty: qty(l.accepted_qty),
    poPrice: Number(l.unit_price), invoicePrice: Number(l.unit_price)
  }))
  openForm({
    title: `收到发票 - ${d.value.gr_no}`,
    width: '820px',
    fields: [
      { prop: 'date', label: '收票日期', type: 'date', required: true },
      { prop: 'lines', label: '发票单价（不含税）：与订单价差异超过 3% 需财务审批', type: 'lines', fixed: true,
        columns: [
          { prop: 'material', label: '物料', type: 'text', width: 220 }, { prop: 'qty', label: '合格数量', type: 'text', width: 90 },
          { prop: 'poPrice', label: '订单价', type: 'text', width: 90 }, { prop: 'invoicePrice', label: '发票单价', precision: 4 }] }
    ],
    initial: { date: today(), lines },
    onSubmit: async m => {
      const prices = Object.fromEntries(m.lines.map(l => [l.id, l.invoicePrice]))
      const inv = await http.post(`/purchase/receipts/${props.id}/invoice`, { date: m.date, prices })
      const v = Number(inv.variance)
      ElMessage.success(inv.status === 'PENDING_APPROVAL' ? `价差 ${money(v)} 超过容差，发票已提交财务审批`
        : v ? `应付已生成，价差 ${money(v)} 已按批次在库比例分摊到存货与成本` : '应付已生成')
      await load()
      emit('changed')
    }
  })
}

onMounted(load)
</script>

<style scoped>
.actions { margin: 12px 0; }
</style>
