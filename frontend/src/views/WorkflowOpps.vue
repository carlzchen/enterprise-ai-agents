<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar"><span class="muted">一个商机贯穿从询价到回款。每一步是谁做的、发了什么、现在等谁，都在这里。</span><span class="spacer" /><el-button size="small" @click="load">刷新</el-button></div>
      <el-table :data="rows" size="small" border stripe height="calc(100vh - 230px)" @row-click="open">
        <el-table-column prop="opp_id" label="商机号" width="130" />
        <el-table-column prop="customer_name" label="客户" min-width="200" show-overflow-tooltip />
        <el-table-column prop="title" label="内容" min-width="240" show-overflow-tooltip />
        <el-table-column label="状态" width="120"><template #default="{ row }"><el-tag size="small" :type="TYPE[row.status]">{{ row.status_cn }}</el-tag></template></el-table-column>
        <el-table-column prop="next_action" label="下一步" min-width="260" show-overflow-tooltip />
        <el-table-column label="到期" width="130"><template #default="{ row }">{{ fmt(row.next_due_at) }}</template></el-table-column>
        <el-table-column prop="open_tasks" label="待办" width="60" align="right" />
        <el-table-column prop="owner" label="负责人" width="90" />
      </el-table>
    </div>
    <el-drawer v-model="show" :title="d ? `${d.opportunity.opp_id}｜${d.opportunity.customer_name}` : ''" size="760px">
      <template v-if="d">
        <div class="bar">
          <el-tag :type="TYPE[d.opportunity.status]">{{ d.opportunity.status_cn }}</el-tag><span class="muted">{{ d.opportunity.title }}</span><span class="spacer" />
          <el-button v-if="!['LOST', 'WON', 'ORDERED', 'DELIVERED'].includes(d.opportunity.status)" size="small" type="danger" plain @click="close">关闭（输单或取消）</el-button>
        </div>
        <el-steps :active="d.history.length" finish-status="success" size="small" align-center class="steps">
          <el-step v-for="(h, i) in d.history" :key="i" :title="h.to_cn" :description="`${fmt(h.at).slice(5)} ${h.actor}`" />
        </el-steps>
        <template v-if="d.quotes.length">
          <h4>报价</h4>
          <el-table :data="d.quotes" size="small" border>
            <el-table-column label="报价单" width="150"><template #default="{ row }">{{ row.quote_no }} 第 {{ row.version }} 版</template></el-table-column>
            <el-table-column prop="model" label="型号" min-width="140" />
            <el-table-column label="阶梯价" min-width="230"><template #default="{ row }">{{ row.tiers.map(t => `${t.qty}+：${money(t.unit_price)}`).join('　') }}</template></el-table-column>
            <el-table-column label="状态" width="90"><template #default="{ row }">{{ QS[row.status] || row.status }}</template></el-table-column>
            <el-table-column prop="valid_until" label="有效期" width="100" />
          </el-table>
        </template>
        <template v-if="d.negotiations.length">
          <h4>议价</h4>
          <div v-for="n in d.negotiations" :key="n.round" class="line">第 {{ n.round }} 轮：客户目标价 {{ n.target_price ?? '未给出' }}，我方决定 {{ STRATEGY[n.strategy] || '（还没决定）' }}<template v-if="n.new_price">，新价格 {{ money(n.new_price) }}</template></div>
        </template>
        <template v-if="d.orders.length">
          <h4>订单</h4>
          <div v-for="o in d.orders" :key="o.id" class="line">PO {{ o.po_no }}　金额 {{ money(o.data.amount) }}　评审 {{ { REVIEWING: '进行中', PASSED: '已通过', REJECTED: '不通过' }[o.review_status] }}
            <template v-if="o.issues.length">（{{ o.issues.length }} 项差异）</template><template v-if="o.erp_order_no">　ERP 订单 {{ o.erp_order_no }}</template></div>
        </template>
        <h4>往来邮件</h4>
        <div v-for="x in timeline" :key="x.key" class="line" :class="x.dir">
          <el-tag size="small" :type="x.dir === 'out' ? 'success' : 'info'" effect="plain">{{ x.dir === 'out' ? '我方' : '来信' }}</el-tag>
          <span class="muted">{{ fmt(x.at).slice(5) }}</span> {{ x.text }}
          <el-tag v-if="x.adoption" size="small" effect="plain" type="info">{{ ADOPT_CN[x.adoption] }}</el-tag>
          <pre v-if="x.body" class="mail">{{ x.body }}</pre>
        </div>
        <h4>待办记录</h4>
        <el-table :data="d.tasks" size="small" border>
          <el-table-column prop="title" label="事项" min-width="280" show-overflow-tooltip />
          <el-table-column label="角色" width="70"><template #default="{ row }">{{ ROLE_CN[row.role] }}</template></el-table-column>
          <el-table-column label="状态" width="80"><template #default="{ row }">{{ { OPEN: '待处理', DONE: '已处理', CANCELLED: '已取消' }[row.status] }}</template></el-table-column>
          <el-table-column prop="done_by" label="处理人" width="80" /><el-table-column prop="result" label="结果" min-width="140" show-overflow-tooltip />
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { ADOPT_CN, OB_CN, ROLE_CN, fmt, money } from '../api/workflow'

const TYPE = { NEW: 'info', CLARIFYING: 'warning', CONFIRMED: '', QUOTED: '', SAMPLING: 'warning', SAMPLE_APPROVED: 'success', ORDERED: 'success', DELIVERED: 'success', WON: 'success', LOST: 'danger' }
const QS = { DRAFT: '草稿', APPROVING: '审批中', APPROVED: '已批准', SENT: '已发出', REJECTED: '被驳回', SUPERSEDED: '已作废' }
const STRATEGY = { ACCEPT: '接受', PARTIAL: '部分让价', TRADE_TERMS: '条件换价格', TRADE_SOLUTION: '方案换价格', ACCEPT_STRATEGIC: '战略性接受', DECLINE: '放弃', ASK_TARGET: '先问目标价' }
const rows = ref([])
const show = ref(false)
const d = ref(null)

const timeline = computed(() => [
  ...d.value.mails.map(m => ({ key: m.message_id, dir: 'in', at: m.received_at, text: `${m.subject}　${m.summary || ''}` })),
  ...d.value.outbound.filter(o => o.status !== 'PENDING').map(o => ({ key: o.action_id, dir: 'out', at: o.sent_at || '', text: `${OB_CN[o.kind]}（${o.drafted_by === 'llm' ? '模型起草' : '模板'}，${o.confirmed_by} 确认）`, adoption: o.adoption, body: o.status === 'SENT' ? o.final : '' }))
].sort((a, b) => String(a.at).localeCompare(String(b.at))))

async function load() {
  rows.value = await api.get('/opportunities')
}
async function open(row) {
  d.value = await api.get(`/opportunities/${row.opp_id}`)
  show.value = true
}
async function close() {
  const r = await ElMessageBox.prompt('关闭的原因（价格、交期、技术、客户取消……）', '关闭商机', { inputValidator: v => (v && v.length >= 2) || '请写原因' }).catch(() => null)
  if (!r) return
  await api.post(`/opportunities/${d.value.opportunity.opp_id}/close`, { reason: r.value })
  ElMessage.success('已关闭')
  show.value = false
  load()
}
onMounted(load)
</script>

<style scoped>
.toolbar, .bar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; font-size: 12px; }
.spacer { flex: 1; }
.steps { margin: 14px 0; }
h4 { margin: 16px 0 6px; }
.line { font-size: 13px; margin: 6px 0; }
.mail { background: #f6f8fa; border-radius: 6px; padding: 8px 10px; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 4px 0 0; font-family: inherit; max-height: 180px; overflow: auto; }
:deep(.el-table__row) { cursor: pointer; }
</style>
