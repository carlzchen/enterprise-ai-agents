<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="period" type="month" value-format="YYYY-MM" :clearable="false" />
        <el-button type="primary" @click="run">对账</el-button>
        <span class="muted">工商银行基本户｜已按回单入账的流水直接匹配，其余先按"金额 + 附言（发票号）"匹配（覆盖长假延迟），再按"金额相等、日期相差 ≤ 7 天"匹配；截至期末仍未匹配的即未达账项</span>
      </div>
      <template v-if="r">
        <el-alert :type="r.balanced ? 'success' : 'error'" :closable="false" :title="r.balanced ? `调节后余额一致：${money(r.adjustedBook)}` : '调节后余额不一致，请检查'" style="margin-bottom: 12px" />
        <div class="grid">
          <el-descriptions title="企业账面" :column="1" border size="small">
            <el-descriptions-item label="银行存款日记账余额">{{ money(r.bookBalance) }}</el-descriptions-item>
            <el-descriptions-item label="加：银行已收、企业未收">{{ money(r.bankReceivedNotBooked) }}</el-descriptions-item>
            <el-descriptions-item label="减：银行已付、企业未付">{{ money(-r.bankPaidNotBooked) }}</el-descriptions-item>
            <el-descriptions-item label="调节后余额"><b>{{ money(r.adjustedBook) }}</b></el-descriptions-item>
          </el-descriptions>
          <el-descriptions title="银行对账单" :column="1" border size="small">
            <el-descriptions-item label="对账单余额">{{ money(r.bankBalance) }}</el-descriptions-item>
            <el-descriptions-item label="加：企业已收、银行未收">{{ money(r.bookReceivedNotBank) }}</el-descriptions-item>
            <el-descriptions-item label="减：企业已付、银行未付">{{ money(-r.bookPaidNotBank) }}</el-descriptions-item>
            <el-descriptions-item label="调节后余额"><b>{{ money(r.adjustedBank) }}</b></el-descriptions-item>
          </el-descriptions>
        </div>
        <h4>未达账项：企业已记、银行未记（{{ r.bookOnly.length }}）</h4>
        <el-table :data="r.bookOnly" size="small" border max-height="220">
          <el-table-column prop="voucher_no" label="凭证号" width="140" /><el-table-column prop="voucher_date" label="日期" width="100" />
          <el-table-column prop="summary" label="摘要" /><el-table-column label="金额" align="right" width="140"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
        </el-table>
        <h4>未达账项：银行已记、企业未记（{{ r.bankOnly.length }}）</h4>
        <el-table :data="r.bankOnly" size="small" border max-height="220">
          <el-table-column prop="txn_date" label="日期" width="100" /><el-table-column prop="memo" label="摘要" />
          <el-table-column label="金额" align="right" width="140"><template #default="{ row }">{{ money(row.amount) }}</template></el-table-column>
          <el-table-column v-if="hasRole(['ADMIN','FINANCE'])" label="" width="110"><template #default="{ row }">
            <el-button link type="primary" size="small" @click="book(row)">依据回单入账</el-button></template></el-table-column>
        </el-table>
      </template>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { money } from '../api/format'

const period = ref('2026-08')
const r = ref(null)
async function run() { r.value = await http.post('/treasury/reconcile', null, { params: { period: period.value } }) }
async function book(row) { await http.post(`/treasury/statements/${row.id}/book`); ElMessage.success('已入账'); run() }
onMounted(run)
</script>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
h4 { margin: 16px 0 8px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
</style>
