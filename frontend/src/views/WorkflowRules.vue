<template>
  <div class="page">
    <div class="page-card">
      <div class="stats" v-if="s">
        <div><div class="num">{{ s.mails_total }}</div><div class="muted">收到的邮件</div></div>
        <div><div class="num">{{ s.mails.PENDING || 0 }}</div><div class="muted">待人工确认</div></div>
        <div><div class="num">{{ s.mails_corrected }}</div><div class="muted">被人工修正过</div></div>
        <div><div class="num">{{ s.adoption_rate == null ? '-' : (s.adoption_rate * 100).toFixed(0) + '%' }}</div><div class="muted">起草内容采纳率</div></div>
        <div><div class="num">{{ s.adoption.AS_IS || 0 }} / {{ s.adoption.EDITED || 0 }} / {{ s.adoption.DISCARDED || 0 }}</div><div class="muted">直接采用 / 修改后采用 / 弃用</div></div>
        <div><div class="num">{{ s.selection.adopted }} / {{ s.selection.confirmed }}</div><div class="muted">选型推荐被采纳</div></div>
        <div><div class="num" :class="{ bad: s.tasks_overdue }">{{ s.tasks_open }}（{{ s.tasks_overdue }}）</div><div class="muted">待办（其中超时）</div></div>
      </div>
      <div class="muted" v-if="s">商机：<span v-for="(n, k) in s.opportunities" :key="k" class="chip">{{ k }} {{ n }}</span>　邮件来源：{{ s.mailbox }}　自动写 ERP：{{ s.erp_write ? '开' : '关（人工导入）' }}</div>
      <h4>规则 <span class="muted">价格、审批、核对都按这里的规则由程序计算，大模型不参与。修改会生成新版本，旧版本保留。{{ canEdit ? '' : '（只有管理层和财务可以改）' }}</span></h4>
      <el-collapse>
        <el-collapse-item v-for="r in rules" :key="r.name" :name="r.name">
          <template #title><b>{{ NAMES[r.name] || r.name }}</b><span class="muted">　第 {{ r.version }} 版　{{ r.updated_by }}　{{ fmt(r.updated_at) }}　{{ r.content['说明'] }}</span></template>
          <el-input v-model="r.text" type="textarea" :rows="Math.min(14, r.text.split('\n').length + 1)" :disabled="!canEdit" class="code" />
          <el-button v-if="canEdit" type="primary" size="small" style="margin-top: 6px" @click="save(r)">保存为新版本</el-button>
        </el-collapse-item>
      </el-collapse>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { auth } from '../api/auth'
import api, { fmt } from '../api/workflow'

const NAMES = { margin: '毛利率', tiers: '数量阶梯', approval: '审批层级', customer_level: '客户等级', required_params: '询价必填参数', selection: '选型容差', quote: '报价有效期',
  po_review: 'PO 核对', sla_hours: '待办时限', autosend: '自动外发', supplier_domains: '供应商邮箱域名' }
const s = ref(null)
const rules = ref([])
const canEdit = computed(() => ['ADMIN', 'MANAGEMENT', 'FINANCE'].includes(auth.user?.role))

async function load() {
  s.value = await api.get('/stats')
  rules.value = (await api.get('/rules')).map(r => ({ ...r, text: JSON.stringify(r.content, null, 2) }))
}
async function save(r) {
  let content
  try {
    content = JSON.parse(r.text)
  } catch {
    return ElMessage.error('格式不对，请检查括号和逗号')
  }
  const res = await api.put(`/rules/${r.name}`, { content })
  ElMessage.success(`已保存为第 ${res.version} 版，之后的计算用新规则`)
  load()
}
onMounted(load)
</script>

<style scoped>
.stats { display: flex; gap: 28px; flex-wrap: wrap; margin-bottom: 10px; }
.num { font-size: 20px; font-weight: 600; color: #2c3e50; }
.num.bad { color: #c45656; }
.chip { margin-right: 10px; }
h4 { margin: 18px 0 8px; }
h4 .muted { font-weight: normal; font-size: 12px; }
.code :deep(textarea) { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
</style>
