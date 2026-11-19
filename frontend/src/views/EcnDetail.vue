<template>
  <div v-if="d">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="ECN 编号">{{ d.ecn_no }}</el-descriptions-item>
      <el-descriptions-item label="状态"><el-tag size="small" :type="ECN_STATUS[d.status]?.[1]">{{ ECN_STATUS[d.status]?.[0] }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="变更原因">{{ ECN_REASONS[d.reason] }}</el-descriptions-item>
      <el-descriptions-item label="标题" :span="3">{{ d.title }}</el-descriptions-item>
      <el-descriptions-item label="变更说明" :span="3">{{ d.description }}</el-descriptions-item>
      <el-descriptions-item label="申请人">{{ d.requested_by }}</el-descriptions-item>
      <el-descriptions-item label="申请日期">{{ d.request_date }}</el-descriptions-item>
      <el-descriptions-item label="生效日期">{{ d.effective_date || '-' }}</el-descriptions-item>
    </el-descriptions>

    <div class="actions">
      <el-button v-if="d.status === 'DRAFT' && hasRole(['ADMIN','ENGINEER'])" type="primary" @click="post('submit', '已提交会签')">提交会签</el-button>
      <template v-if="d.status === 'REVIEW' && myPending">
        <el-button type="success" @click="review(true)">同意（{{ ROLES_CN[myPending.review_role] }}）</el-button>
        <el-button type="danger" plain @click="review(false)">否决</el-button>
      </template>
      <el-button v-if="d.status === 'APPROVED' && hasRole(['ADMIN','ENGINEER'])" type="primary" @click="release">发布生效（升级 BOM 版本）</el-button>
    </div>

    <h4>变更明细</h4>
    <el-table :data="d.lines" size="small" border>
      <el-table-column prop="product_code" label="产品" min-width="160" />
      <el-table-column label="动作" width="100"><template #default="{ row }">{{ ECN_ACTIONS[row.action] }}</template></el-table-column>
      <el-table-column prop="material_code" label="物料" width="120" />
      <el-table-column prop="material_name" label="物料名称" min-width="170" />
      <el-table-column label="新用量" align="right" width="100"><template #default="{ row }">{{ row.new_qty == null ? '-' : qty(row.new_qty) }} {{ row.new_qty == null ? '' : row.uom }}</template></el-table-column>
    </el-table>

    <h4>会签</h4>
    <el-table :data="d.reviews" size="small" border empty-text="尚未提交会签">
      <el-table-column label="会签方" width="90"><template #default="{ row }">{{ ROLES_CN[row.review_role] }}</template></el-table-column>
      <el-table-column label="结论" width="90"><template #default="{ row }"><el-tag size="small" :type="REVIEW_DECISION[row.decision]?.[1]">{{ REVIEW_DECISION[row.decision]?.[0] }}</el-tag></template></el-table-column>
      <el-table-column prop="reviewer" label="会签人" width="100" />
      <el-table-column prop="review_date" label="日期" width="110" />
      <el-table-column prop="comment" label="意见" min-width="200" />
    </el-table>

    <h4>影响分析 <span class="muted">（按当月物料参考价、近 12 个月销量{{ d.status === 'RELEASED' ? '；已发布：对比变更前后 BOM 版本' : '；未发布：在当前 BOM 上模拟' }}）</span></h4>
    <div v-if="impact" class="impact">
      <el-row :gutter="12" class="impact-kpi">
        <el-col :span="8"><div class="box"><div class="muted">平均单台材料成本变化</div><b :class="cls(impact.avgUnitCostDelta)">{{ signed(impact.avgUnitCostDelta, 4) }} 元</b></div></el-col>
        <el-col :span="8"><div class="box"><div class="muted">年度成本影响</div><b :class="cls(impact.annualImpact)">{{ signed(impact.annualImpact, 2) }} 元</b></div></el-col>
        <el-col :span="8"><div class="box"><div class="muted">呆滞物料风险</div><b :class="Number(impact.obsoleteValue) > 0 ? 'bad' : ''">{{ money(impact.obsoleteValue) }} 元</b></div></el-col>
      </el-row>
      <el-table :data="impact.products" size="small" border>
        <el-table-column prop="productCode" label="产品" min-width="160" />
        <el-table-column label="版本" width="80"><template #default="{ row }">{{ row.currentRevision }} → {{ row.nextRevision }}</template></el-table-column>
        <el-table-column label="单台材料成本" align="right"><template #default="{ row }">{{ row.unitCostBefore }} → {{ row.unitCostAfter }}</template></el-table-column>
        <el-table-column label="单台变化" align="right" width="100"><template #default="{ row }"><span :class="cls(row.unitCostDelta)">{{ signed(row.unitCostDelta, 4) }}</span></template></el-table-column>
        <el-table-column label="近12月销量" align="right" width="110"><template #default="{ row }">{{ qty(row.annualVolume) }}</template></el-table-column>
        <el-table-column label="年度影响" align="right"><template #default="{ row }"><span :class="cls(row.annualImpact)">{{ signed(row.annualImpact, 2) }}</span></template></el-table-column>
        <el-table-column label="在制工单" align="right" width="90"><template #default="{ row }">{{ row.openWorkOrders }}</template></el-table-column>
      </el-table>
      <p v-if="impact.obsoleteMaterials.length" class="muted">
        将停用物料：<span v-for="m in impact.obsoleteMaterials" :key="m.code">{{ m.code }} {{ m.name }}（库存 {{ qty(m.qty) }}，{{ money(m.value) }} 元）；</span>
      </p>
      <p v-if="d.status !== 'RELEASED'" class="muted">说明：发布后在制工单的后续报工按新 BOM 倒冲领料。</p>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { auth, hasRole } from '../api/auth'
import { ECN_STATUS, ECN_REASONS, ECN_ACTIONS, REVIEW_DECISION, ROLES_CN, money, qty } from '../api/format'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const d = ref(null)
const impact = ref(null)

const myPending = computed(() => {
  if (!d.value || !auth.user) return null
  const pending = d.value.reviews.filter(r => r.decision === 'PENDING')
  if (auth.user.role === 'ADMIN') return pending[0] || null
  return pending.find(r => r.review_role === auth.user.role) || null
})

async function load() {
  d.value = await http.get(`/plm/ecns/${props.id}`)
  impact.value = await http.get(`/plm/ecns/${props.id}/impact`)
}

async function post(action, msg) {
  await http.post(`/plm/ecns/${props.id}/${action}`)
  ElMessage.success(msg)
  await load()
  emit('changed')
}

async function review(approve) {
  const { value } = await ElMessageBox.prompt(approve ? '会签意见（可选）' : '否决原因（必填）', approve ? '同意' : '否决', {
    inputValue: approve ? '同意' : '', inputValidator: v => approve || (v && v.trim().length > 0) || '请填写否决原因'
  })
  await http.post(`/plm/ecns/${props.id}/review`, { approve, comment: value, reviewRole: myPending.value.review_role })
  ElMessage.success('会签完成')
  await load()
  emit('changed')
}

async function release() {
  await ElMessageBox.confirm('发布后将为受影响产品生成新 BOM 版本，生产立即按新 BOM 执行。确认发布？', '发布 ECN', { type: 'warning' })
  await post('release', '已发布，BOM 已升版')
}

const signed = (v, digits) => (Number(v) > 0 ? '+' : '') + Number(v).toFixed(digits)
const cls = v => (Number(v) < 0 ? 'good' : Number(v) > 0 ? 'bad' : '')

onMounted(load)
</script>

<style scoped>
.actions { margin: 12px 0; display: flex; gap: 8px; }
h4 { margin: 16px 0 8px; }
.impact-kpi { margin-bottom: 10px; }
.box { background: #f5f7fa; border-radius: 6px; padding: 10px 12px; }
.box b { font-size: 18px; }
.good { color: #3d8c5a; }
.bad { color: #c0392b; }
</style>
