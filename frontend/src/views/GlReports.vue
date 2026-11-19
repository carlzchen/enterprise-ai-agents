<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" :clearable="false" @change="load" />
        <el-tag :type="tb.balanced ? 'success' : 'danger'">{{ tb.balanced ? '试算平衡' : '试算不平衡' }}</el-tag>
        <span class="muted">本期借方 {{ money(tb.periodDebit) }}｜贷方 {{ money(tb.periodCredit) }}</span>
      </div>
      <el-row :gutter="12">
        <el-col :span="15">
          <h4>科目余额表</h4>
          <el-table :data="tb.rows" size="small" border max-height="560">
            <el-table-column label="科目" min-width="170"><template #default="{ row }">{{ row.code }} {{ row.name }}</template></el-table-column>
            <el-table-column label="期初余额" align="right"><template #default="{ row }">{{ money(row.opening) }}</template></el-table-column>
            <el-table-column label="本期借方" align="right"><template #default="{ row }">{{ money(row.period_debit) }}</template></el-table-column>
            <el-table-column label="本期贷方" align="right"><template #default="{ row }">{{ money(row.period_credit) }}</template></el-table-column>
            <el-table-column label="期末余额" align="right"><template #default="{ row }">{{ money(row.closing) }}</template></el-table-column>
          </el-table>
        </el-col>
        <el-col :span="9">
          <h4>利润表</h4>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item v-for="r in incomeRows" :key="r[0]" :label="r[0]">
              <span :style="{ fontWeight: r[2] ? 600 : 400 }">{{ money(is[r[1]]) }}</span>
            </el-descriptions-item>
          </el-descriptions>
          <h4>总账与子账对账</h4>
          <el-table :data="rec" size="small" border>
            <el-table-column prop="account_name" label="科目" />
            <el-table-column label="总账余额" align="right"><template #default="{ row }">{{ money(row.gl_balance) }}</template></el-table-column>
            <el-table-column label="子账余额" align="right"><template #default="{ row }">{{ money(row.subledger_balance) }}</template></el-table-column>
            <el-table-column label="差异" align="right"><template #default="{ row }">{{ money(row.difference) }}</template></el-table-column>
          </el-table>
          <p class="muted small">应收 / 应付 / 应付暂估应为 0 差异；存货差异来自移动加权平均单价保留 4 位小数的舍入，属合理尾差。</p>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import http from '../api/http'
import { money } from '../api/format'

const y = new Date().getFullYear()
const range = ref([`${y}-01-01`, new Date().toISOString().slice(0, 10)])
const tb = ref({ rows: [] })
const is = ref({})
const rec = ref([])
const incomeRows = [['营业收入', 'revenue'], ['　其中：其他业务收入（保外维修）', 'otherRevenue'], ['营业成本', 'cogs'], ['　其中：其他业务成本', 'otherCost'], ['毛利', 'grossProfit', true], ['税金及附加', 'taxesAndSurcharges'], ['销售费用', 'sellingExpense'],
  ['管理费用', 'adminExpense'], ['研发费用', 'rndExpense'], ['财务费用', 'financeExpense'], ['资产处置收益', 'assetDisposalGain'], ['营业外支出', 'nonOperating'],
  ['利润总额', 'totalProfit', true]]

async function load() {
  const [from, to] = range.value
  ;[tb.value, is.value, rec.value] = await Promise.all([
    http.get('/gl/trial-balance', { params: { from, to } }), http.get('/gl/income-statement', { params: { from, to } }),
    http.get('/gl/reconciliation')])
}
onMounted(load)
</script>

<style scoped>
h4 { margin: 8px 0; }
.small { font-size: 12px; }
</style>
