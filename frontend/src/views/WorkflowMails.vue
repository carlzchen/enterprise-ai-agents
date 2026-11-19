<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-radio-group v-model="status" size="small" @change="load">
          <el-radio-button value="">全部</el-radio-button><el-radio-button value="PENDING">待确认</el-radio-button>
          <el-radio-button value="TRIAGED">已分派</el-radio-button><el-radio-button value="ARCHIVED">已归档</el-radio-button>
        </el-radio-group>
        <span class="muted">每封业务邮件先在这里识别出是什么事、哪个客户，再分给对应的流程和负责人。拿不准的进「待确认」。</span>
        <span class="spacer" />
        <el-button size="small" type="primary" :loading="pulling" @click="pull">收取新邮件</el-button>
      </div>
      <el-table :data="rows" size="small" border stripe height="calc(100vh - 230px)" @row-click="open">
        <el-table-column label="时间" width="130"><template #default="{ row }">{{ fmt(row.received_at) }}</template></el-table-column>
        <el-table-column label="往来方" min-width="170" show-overflow-tooltip><template #default="{ row }">
          <span v-if="row.party_name">{{ row.party_name }}</span><span v-else class="bad">认不出（{{ row.real_from || row.from_addr }}）</span>
        </template></el-table-column>
        <el-table-column prop="subject" label="主题" min-width="200" show-overflow-tooltip />
        <el-table-column label="识别出的事" min-width="190"><template #default="{ row }">
          <el-tag v-for="i in row.intents" :key="i.intent" size="small" :type="i.confidence < 0.75 ? 'warning' : ''" effect="plain" class="tag">{{ i.intent_cn }} {{ (i.confidence * 100).toFixed(0) }}%</el-tag>
        </template></el-table-column>
        <el-table-column prop="summary" label="系统的理解" min-width="280" show-overflow-tooltip />
        <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag size="small" :type="ST[row.status]?.type">{{ ST[row.status]?.text || row.status }}</el-tag></template></el-table-column>
        <el-table-column prop="opp_id" label="商机" width="130" />
      </el-table>
    </div>
    <el-drawer v-model="show" :title="m?.subject" size="680px">
      <template v-if="m">
        <div class="bar">
          <el-tag size="small" :type="ST[m.status]?.type">{{ ST[m.status]?.text }}</el-tag>
          <el-tag size="small" effect="plain">{{ m.party_name || '认不出往来方' }}</el-tag>
          <el-tag v-if="m.real_from" size="small" type="info" effect="plain">转发邮件，真实发件人 {{ m.real_from }}</el-tag>
          <el-tag size="small" type="info" effect="plain">{{ m.lang === 'en' ? '英文' : '中文' }}</el-tag>
        </div>
        <div v-if="m.pending_reason" class="note">待确认的原因：{{ m.pending_reason }}（请到「待办中心」处理）</div>
        <div class="muted">{{ m.from_name }} &lt;{{ m.from_addr }}&gt;　{{ fmt(m.received_at) }}</div>
        <pre class="mail">{{ m.body_new }}</pre>
        <div v-for="a in m.attachments" :key="a.name"><el-tag size="small" type="info">附件 {{ a.name }}</el-tag><pre v-if="a.preview" class="mail small">{{ a.preview }}</pre></div>
        <h4>系统读出来的内容</h4>
        <div class="muted">{{ m.summary }}</div>
        <div v-for="i in m.intents" :key="i.intent" class="box">
          <b>{{ i.intent_cn }}</b>　<span class="muted">把握 {{ (i.confidence * 100).toFixed(0) }}%<template v-if="i.corrected_by">　已由 {{ i.corrected_by }} 人工确认</template></span>
          <pre v-if="i.extracted && Object.keys(i.extracted).length" class="mail small">{{ JSON.stringify(i.extracted, null, 1) }}</pre>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import api, { fmt } from '../api/workflow'

const ST = { NEW: { text: '处理中', type: 'info' }, PENDING: { text: '待确认', type: 'warning' }, TRIAGED: { text: '已分派', type: 'success' }, ARCHIVED: { text: '已归档', type: 'info' }, FAILED: { text: '失败', type: 'danger' } }
const status = ref('')
const rows = ref([])
const show = ref(false)
const m = ref(null)
const pulling = ref(false)

async function load() {
  rows.value = await api.get('/mails', { params: status.value ? { status: status.value } : {} })
}
async function open(row) {
  m.value = await api.get('/mails/detail', { params: { id: row.message_id } })
  show.value = true
}
async function pull() {
  pulling.value = true
  try {
    const r = await api.post('/mails/pull')
    ElMessage.success(r.new ? `收到 ${r.new} 封新邮件，正在识别` : '没有新邮件')
    setTimeout(load, 4000)
  } finally {
    pulling.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; font-size: 12px; }
.spacer { flex: 1; }
.tag { margin-right: 4px; }
.bad { color: #c45656; }
.bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.note { background: #fdf6ec; color: #8a6d3b; border-left: 3px solid #e6a23c; padding: 6px 10px; margin: 8px 0; font-size: 13px; }
.mail { background: #f6f8fa; border-radius: 6px; padding: 8px 10px; font-size: 13px; white-space: pre-wrap; word-break: break-all; margin: 6px 0; font-family: inherit; }
.mail.small { font-size: 12px; max-height: 200px; overflow: auto; }
.box { border: 1px solid #e5e8ef; border-radius: 6px; padding: 8px 10px; margin-top: 8px; font-size: 13px; }
h4 { margin: 14px 0 6px; }
:deep(.el-table__row) { cursor: pointer; }
</style>
