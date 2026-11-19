<template>
  <div v-if="rfq">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="询价单号">{{ rfq.rfq_no }}</el-descriptions-item>
      <el-descriptions-item label="物料">{{ rfq.material_code }} {{ rfq.material_name }}</el-descriptions-item>
      <el-descriptions-item label="状态">{{ { OPEN: '询价中', AWARDED: '已定标', CANCELLED: '已取消' }[rfq.status] }}</el-descriptions-item>
      <el-descriptions-item label="数量">{{ qty(rfq.qty) }} {{ rfq.uom }}</el-descriptions-item>
      <el-descriptions-item label="需求日期">{{ rfq.need_date }}</el-descriptions-item>
      <el-descriptions-item label="定标">{{ rfq.awarded_supplier_name ? `${rfq.awarded_supplier_name} @ ${rfq.awarded_price}` : '-' }}</el-descriptions-item>
    </el-descriptions>
    <div class="actions" v-if="rfq.status === 'OPEN' && hasRole(['ADMIN','PURCHASE'])">
      <el-button @click="addQuote">录入报价</el-button>
    </div>
    <h4>比价（价格 60% + 交期满足 20% + 近期绩效 20%）</h4>
    <el-table :data="quotes" size="small" border>
      <el-table-column prop="supplier_name" label="供应商" min-width="200" />
      <el-table-column prop="unit_price" label="单价" align="right" />
      <el-table-column prop="lead_days" label="交期(天)" align="right" width="90" />
      <el-table-column label="满足需求日" width="100"><template #default="{ row }">{{ row.meets_need_date ? '是' : '否' }}</template></el-table-column>
      <el-table-column prop="last_score" label="近期绩效" align="right" width="90" />
      <el-table-column label="推荐分" align="right" width="90"><template #default="{ row }"><b>{{ row.recommend_score }}</b></template></el-table-column>
      <el-table-column width="150">
        <template #default="{ row }">
          <el-tag v-if="row.recommended" type="success" size="small">推荐</el-tag>
          <el-button v-if="rfq.status === 'OPEN' && hasRole(['ADMIN','PURCHASE'])" link type="primary" size="small" @click="award(row)">定标</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { openForm, options, opt } from '../api/shared'
import { qty } from '../api/format'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const rfq = ref(null)
const quotes = ref([])

async function load() {
  rfq.value = (await http.get('/views/rfqs', { params: { size: 500 } })).records.find(r => r.id === props.id)
  quotes.value = await http.get(`/srm/rfqs/${props.id}/compare`)
}

async function addQuote() {
  const o = await options()
  openForm({
    title: '录入供应商报价',
    fields: [
      { prop: 'supplierId', label: '供应商', type: 'select', required: true, options: opt(o.suppliers) },
      { prop: 'unitPrice', label: '单价', type: 'number', precision: 4, required: true },
      { prop: 'leadDays', label: '交期(天)', type: 'number', precision: 0, required: true },
      { prop: 'remark', label: '备注' }
    ],
    initial: { leadDays: 7 },
    onSubmit: async m => { await http.post(`/srm/rfqs/${props.id}/quotes`, m); await load() }
  })
}

async function award(row) {
  let reason = null
  if (!row.recommended) {
    reason = (await ElMessageBox.prompt('未选择推荐供应商，请填写定标理由', '定标', { inputValidator: v => !!v?.trim() || '必填' })).value
  } else {
    await ElMessageBox.confirm(`定标给 ${row.supplier_name}，并自动生成采购单？`, '定标')
  }
  const po = await http.post(`/srm/rfqs/${props.id}/award`, { supplierId: row.supplier_id, reason })
  ElMessage.success(`已定标，生成采购单 ${po.poNo}`)
  await load()
  emit('changed')
}

onMounted(load)
</script>

<style scoped>
.actions { margin: 12px 0; }
h4 { margin: 16px 0 8px; }
</style>
