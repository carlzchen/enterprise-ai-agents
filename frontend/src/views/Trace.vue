<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-radio-group v-model="mode">
          <el-radio-button value="delivery">反向：发货单 → 原材料批次</el-radio-button>
          <el-radio-button value="complaint">反向：客诉 → 原材料批次</el-radio-button>
          <el-radio-button value="lot">正向：原材料批次 → 客户</el-radio-button>
        </el-radio-group>
        <el-input v-model="key" :placeholder="placeholder" style="width: 260px" clearable @keyup.enter="run" />
        <el-button type="primary" @click="run">追溯</el-button>
      </div>

      <template v-if="back">
        <el-descriptions :column="4" border size="small" class="head">
          <template v-if="back.complaint">
            <el-descriptions-item label="客诉">{{ back.complaint.complaint_no }}</el-descriptions-item>
            <el-descriptions-item label="客户">{{ back.complaint.customer_name }}</el-descriptions-item>
            <el-descriptions-item label="不良">{{ DEFECTS[back.complaint.defect_type] }}</el-descriptions-item>
            <el-descriptions-item label="根因">{{ back.complaint.root_cause || '分析中' }}</el-descriptions-item>
          </template>
          <el-descriptions-item label="发货单">{{ trace.delivery_no }}</el-descriptions-item>
          <el-descriptions-item label="客户">{{ trace.customer_name }}</el-descriptions-item>
          <el-descriptions-item label="发货日期">{{ trace.delivery_date }}</el-descriptions-item>
        </el-descriptions>
        <div v-for="pl in trace.productLots || []" :key="pl.lot_no" class="lot">
          <div class="lot-head">
            <el-tag>成品批次 {{ pl.lot_no }}</el-tag>
            {{ pl.item_code }} ×{{ qty(pl.qty) }}｜产线 {{ pl.line_code }}｜生产 {{ pl.actual_start }} ~ {{ pl.actual_end }}｜工单良率 {{ pct(pl.yield_rate) }}
          </div>
          <el-table :data="pl.materials" size="small" border>
            <el-table-column prop="lot_no" label="原材料批次（到货单）" width="170" />
            <el-table-column prop="item_code" label="物料" width="110" />
            <el-table-column prop="item_name" label="名称" min-width="160" />
            <el-table-column prop="supplier_name" label="供应商" min-width="180" />
            <el-table-column prop="received_date" label="到货" width="100" />
            <el-table-column label="IQC" width="80"><template #default="{ row }">
              <el-tag size="small" :type="row.iqc_result === 'PASS' ? 'success' : 'info'">{{ row.iqc_result || '期初' }}</el-tag>
            </template></el-table-column>
            <el-table-column label="用量" align="right" width="100"><template #default="{ row }">{{ qty(row.qty) }}</template></el-table-column>
            <el-table-column width="90"><template #default="{ row }">
              <el-button v-if="row.iqc_result" link type="primary" size="small" @click="forward(row.lot_no)">正向追溯</el-button>
            </template></el-table-column>
          </el-table>
        </div>
      </template>

      <template v-if="fwd">
        <el-alert type="warning" :closable="false" show-icon
                  :title="`批次 ${key} 共用于 ${fwd.workOrders.length} 个工单，发往 ${fwd.customers.length} 家客户、${fwd.shipments.length} 张发货单`"
                  :description="fwd.customers.join('、')" />
        <h4>使用该批次的工单</h4>
        <el-table :data="fwd.workOrders" size="small" border>
          <el-table-column prop="wo_no" label="工单（成品批次）" width="150" />
          <el-table-column prop="product_code" label="产品" min-width="160" />
          <el-table-column prop="line_code" label="产线" width="70" />
          <el-table-column label="耗用数量" align="right"><template #default="{ row }">{{ qty(row.material_qty) }}</template></el-table-column>
          <el-table-column label="工单良品" align="right"><template #default="{ row }">{{ qty(row.good_qty) }}</template></el-table-column>
        </el-table>
        <h4>召回 / 排查清单</h4>
        <el-table :data="fwd.shipments" size="small" border>
          <el-table-column prop="delivery_no" label="发货单" width="140" />
          <el-table-column prop="delivery_date" label="发货日期" width="100" />
          <el-table-column prop="customer_name" label="客户" min-width="200" />
          <el-table-column prop="product_lot" label="成品批次" width="150" />
          <el-table-column prop="product_code" label="产品" min-width="150" />
          <el-table-column label="数量" align="right"><template #default="{ row }">{{ qty(row.qty) }}</template></el-table-column>
          <el-table-column prop="complaints" label="已有客诉" align="right" width="90" />
        </el-table>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import http from '../api/http'
import { DEFECTS, pct, qty } from '../api/format'

const route = useRoute()
const mode = ref(route.query.mode || 'complaint')
const key = ref(route.query.key || '')
const back = ref(null)
const fwd = ref(null)
const trace = computed(() => back.value?.trace || back.value || {})
const placeholder = computed(() => ({ delivery: '发货单号，如 DN2606150001', complaint: '客诉单号，如 CC2606150001', lot: '原材料批次（到货单号）' }[mode.value]))

async function run() {
  back.value = fwd.value = null
  if (!key.value) return
  if (mode.value === 'delivery') back.value = await http.get(`/trace/delivery/${key.value}`)
  else if (mode.value === 'lot') fwd.value = await http.get(`/trace/lot/${key.value}`)
  else {
    const res = await http.get('/views/complaints', { params: { kw: key.value, size: 1 } })
    if (!res.records.length) return
    back.value = await http.get(`/trace/complaint/${res.records[0].id}`)
  }
}

function forward(lot) {
  mode.value = 'lot'
  key.value = lot
  run()
}

if (key.value) run()
</script>

<style scoped>
.head { margin-bottom: 12px; }
.lot { margin-bottom: 14px; }
.lot-head { margin-bottom: 6px; display: flex; gap: 8px; align-items: center; font-size: 13px; }
h4 { margin: 14px 0 8px; }
</style>
