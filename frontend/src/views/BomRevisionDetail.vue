<template>
  <div v-if="d">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="产品">{{ d.product_code }} {{ d.product_name }}</el-descriptions-item>
      <el-descriptions-item label="版本">{{ d.revision }}<span v-if="d.previousRevision" class="muted">（上一版 {{ d.previousRevision }}）</span></el-descriptions-item>
      <el-descriptions-item label="状态"><el-tag size="small" :type="REV_STATUS[d.status]?.[1]">{{ REV_STATUS[d.status]?.[0] }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="生效日期">{{ d.effective_date }}</el-descriptions-item>
      <el-descriptions-item label="作废日期">{{ d.obsolete_date || '-' }}</el-descriptions-item>
      <el-descriptions-item label="来源 ECN">{{ d.ecn_no ? `${d.ecn_no} ${d.ecn_title}` : '初始版本' }}</el-descriptions-item>
    </el-descriptions>
    <h4>物料清单{{ d.previousRevision ? `（与 ${d.previousRevision} 版对比）` : '' }}</h4>
    <el-table :data="rows" size="small" border :row-class-name="({ row }) => row.change ? 'chg-' + row.change : ''">
      <el-table-column prop="material_code" label="物料编码" width="130" />
      <el-table-column prop="material_name" label="物料名称" min-width="180" />
      <el-table-column prop="category" label="类别" width="100" />
      <el-table-column label="单台用量" align="right" width="140"><template #default="{ row }">{{ row.qty_per == null ? '-' : qty(row.qty_per) }} {{ row.uom }}</template></el-table-column>
      <el-table-column v-if="d.previousRevision" label="上一版用量" align="right" width="120"><template #default="{ row }">{{ row.previous_qty == null ? '-' : qty(row.previous_qty) }}</template></el-table-column>
      <el-table-column v-if="d.previousRevision" label="差异" width="90">
        <template #default="{ row }"><el-tag v-if="row.change" size="small" :type="TAGS[row.change][1]">{{ TAGS[row.change][0] }}</el-tag></template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import http from '../api/http'
import { REV_STATUS, qty } from '../api/format'

const props = defineProps({ id: Number })
const d = ref(null)
const TAGS = { ADDED: ['新增', 'success'], CHANGED: ['用量变化', 'warning'], REMOVED: ['删除', 'danger'] }
const rows = computed(() => [...(d.value?.lines || []), ...(d.value?.removed || [])])

onMounted(async () => {
  d.value = await http.get(`/plm/bom-revisions/${props.id}`)
})
</script>

<style scoped>
h4 { margin: 16px 0 8px; }
:deep(.chg-ADDED) { background: #eef8f1 !important; }
:deep(.chg-CHANGED) { background: #fdf6ec !important; }
:deep(.chg-REMOVED) { background: #fdecec !important; text-decoration: line-through; }
</style>
