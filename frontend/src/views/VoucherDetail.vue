<template>
  <div v-if="v">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="凭证号">{{ v.voucher_no }}</el-descriptions-item>
      <el-descriptions-item label="日期">{{ v.voucher_date }}</el-descriptions-item>
      <el-descriptions-item label="来源">{{ v.source_type }} {{ v.source_no || '' }}</el-descriptions-item>
      <el-descriptions-item label="摘要" :span="3">{{ v.summary }}</el-descriptions-item>
    </el-descriptions>
    <el-table :data="v.entries" size="small" border style="margin-top: 12px" show-summary :summary-method="sum">
      <el-table-column prop="line_no" label="行" width="50" />
      <el-table-column label="科目" min-width="180"><template #default="{ row }">{{ row.account_code }} {{ row.account_name }}</template></el-table-column>
      <el-table-column prop="summary" label="摘要" min-width="160" />
      <el-table-column label="借方" align="right"><template #default="{ row }">{{ Number(row.debit) ? money(row.debit) : '' }}</template></el-table-column>
      <el-table-column label="贷方" align="right"><template #default="{ row }">{{ Number(row.credit) ? money(row.credit) : '' }}</template></el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import http from '../api/http'
import { money } from '../api/format'

const props = defineProps({ id: Number })
const v = ref(null)
const sum = ({ data }) => ['合计', '', '', money(data.reduce((a, r) => a + Number(r.debit), 0)), money(data.reduce((a, r) => a + Number(r.credit), 0))]
onMounted(async () => { v.value = await http.get(`/gl/vouchers/${props.id}`) })
</script>
