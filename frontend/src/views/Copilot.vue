<template>
  <div class="copilot">
    <aside class="sessions">
      <el-button type="primary" style="width: 100%" @click="newSession">新对话</el-button>
      <div v-for="s in sessions" :key="s.session_id" class="session" :class="{ active: s.session_id === sessionId }" @click="openSession(s.session_id)">
        {{ s.title }}
      </div>
    </aside>
    <section class="chat">
      <div ref="scroller" class="messages">
        <div v-if="!messages.length" class="empty">
          <h3>经营与人力数据分析 Copilot</h3>
          <p class="muted">用一句话查制度、查数据、问原因。数字来自 ERP，按你的角色显示可见范围；只查询，不修改任何数据。</p>
          <div v-for="g in examples" :key="g.title" class="ex-group">
            <div class="ex-title">{{ g.title }}</div>
            <el-tag v-for="q in g.items" :key="q" class="example" effect="plain" @click="ask(q)">{{ q }}</el-tag>
          </div>
        </div>
        <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
          <div class="bubble">
            <div v-if="m.role === 'user'">{{ m.text }}</div>
            <template v-else>
              <div v-if="m.intent" class="tags">
                <el-tag size="small" :type="statusType(m.status)">{{ m.intent }}<span v-if="m.sub_intent"> · {{ m.sub_intent }}</span></el-tag>
                <el-tag v-if="m.template_id" size="small" type="info" effect="plain">模板 {{ m.template_id }}</el-tag>
                <el-tag v-if="m.period" size="small" type="info" effect="plain">{{ m.period }}</el-tag>
                <el-tag v-if="m.route_by" size="small" type="info" effect="plain">{{ { rule: '规则路由', llm: '模型路由', cache: '路由缓存' }[m.route_by] }}</el-tag>
                <el-tag v-if="m.cache_hit" size="small" type="success" effect="plain">结果缓存</el-tag>
              </div>
              <div v-if="m.text" class="md" v-html="render(m.text)" />
              <div v-for="d in m.degraded || []" :key="d" class="degraded">已降级：{{ d }}</div>
              <div v-if="m.pending" class="progress">
                <el-icon class="is-loading"><Loading /></el-icon><span>{{ m.progress }}…</span>
              </div>
              <div v-if="m.options?.length" class="options">
                <el-tag v-for="o in m.options" :key="o" class="example" @click="ask(o)">{{ o }}</el-tag>
              </div>
              <CopilotChart v-if="m.chart" :spec="m.chart" />
              <CopilotTable v-for="(t, j) in m.tables || []" :key="j" :result="t.result" :title="t.title" />
              <div v-if="m.sources?.length" class="sources">
                <div class="muted">依据的制度原文</div>
                <div v-for="s in m.sources" :key="s.index" class="source">
                  <el-tag size="small">[{{ s.index }}]</el-tag>
                  <span>{{ s.doc_title }} · {{ s.section }}</span><span class="muted">版本 {{ s.version }}</span>
                </div>
              </div>
              <el-collapse v-if="m.sql || m.metric_defs?.length" class="detail">
                <el-collapse-item title="这个数是怎么算的">
                  <div v-if="m.metric_defs?.length" class="defs">
                    <div class="muted">统计口径</div>
                    <ul><li v-for="d in m.metric_defs" :key="d">{{ d }}</li></ul>
                  </div>
                  <div v-if="m.scope_notes?.length" class="defs">
                    <div class="muted">你能看到的数据范围</div>
                    <ul><li v-for="d in m.scope_notes" :key="d">{{ d }}</li></ul>
                  </div>
                  <div v-if="m.sql">
                    <div class="muted">实际执行的 SQL（已加上权限过滤和行数上限）</div>
                    <pre class="sql">{{ m.sql }}</pre>
                  </div>
                </el-collapse-item>
              </el-collapse>
              <div v-if="!m.pending && m.audit_id" class="actions">
                <el-button v-if="m.result_id" size="small" :icon="Download" @click="exportResult(m)">导出 Excel</el-button>
                <el-button size="small" :type="m.rating === 1 ? 'success' : ''" :icon="CircleCheck" @click="rate(m, 1)">有用</el-button>
                <el-button size="small" :type="m.rating === -1 ? 'danger' : ''" :icon="CircleClose" @click="rate(m, -1)">不对</el-button>
                <a v-if="m.trace_url && auth.user?.role === 'ADMIN'" :href="m.trace_url" target="_blank" class="trace">查看链路</a>
                <span class="muted cost" v-if="m.elapsed_ms">{{ (m.elapsed_ms / 1000).toFixed(1) }} 秒 · {{ m.usage?.total || 0 }} tokens</span>
              </div>
            </template>
          </div>
        </div>
      </div>
      <div class="input">
        <el-input v-model="input" type="textarea" :rows="2" resize="none" :disabled="busy"
                  placeholder="例如：上个月伺服线的加班率是多少？ 三季度毛利为什么下降？ 迟到多久算旷工？（Enter 发送，Shift+Enter 换行）"
                  @keydown.enter.exact.prevent="ask()" />
        <div class="send">
          <span class="muted">追问时不用重复条件，比如接着问"那步进线呢""换成按季度看"</span>
          <el-button type="primary" :loading="busy" @click="ask()">发送</el-button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, h, nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, CircleClose, Download, Loading } from '@element-plus/icons-vue'
import { auth } from '../api/auth'
import copilot, { chatStream } from '../api/copilot'
import { renderMarkdown } from '../api/markdown'
import CopilotChart from '../components/CopilotChart.vue'
import CopilotTable from '../components/CopilotTable.vue'

const route = useRoute()
const sessions = ref([])
const sessionId = ref(null)
const messages = ref([])
const input = ref('')
const busy = ref(false)
const scroller = ref()

const POLICY = { title: '查制度', items: ['迟到多久算旷工？', '年假有几天？', '报销超过 5000 元要谁审批？', '病假工资怎么发，需要什么证明？'] }
const BY_ROLE = {
  HR: [{ title: '人员与考勤', items: ['上个月伺服线的加班率是多少？', '今年每个月的离职率', '各部门现在分别有多少人？', '今年大家为什么离职？'] },
    { title: '薪酬', items: ['各产线 8 月计件工资和保底补差', '各部门人均工资', '如果计件单价上调 5%，每月人工成本增加多少？'] }],
  PRODUCTION: [{ title: '本部门', items: ['上个月各产线出勤率', '上个月各产线产量和良率', '哪个班组上月加班最多？', '各产线上月 OEE'] }],
  SALES: [{ title: '销售与回款', items: ['今年各月销售额', '哪些客户回款逾期了？', '前十大客户销售额', '三季度各产品系列销量占比'] }],
  FINANCE: [{ title: '经营分析', items: ['三季度各产品系列的收入、成本和毛利率', '三季度毛利为什么比去年同期低？', '今年每个月回款金额', '直接人工成本率趋势'] },
    { title: '测算', items: ['如果单位成本上涨 3%，三季度毛利减少多少？', '如果计件单价上调 5%，每月人工成本增加多少？'] }]
}
BY_ROLE.MANAGEMENT = BY_ROLE.ADMIN = [...BY_ROLE.FINANCE, BY_ROLE.HR[0]]
const examples = computed(() => [...(BY_ROLE[auth.user?.role] || [{ title: '本人数据', items: ['我最近三个月的工资是多少？', '我上个月的考勤情况'] }]), POLICY])

const render = text => renderMarkdown(text).replace(/&gt; (.*?)(<br>|$)/g, '<div class="note">$1</div>').replace(/\[(\d+)\]/g, '<sup class="cite">[$1]</sup>')
const statusType = s => ({ DENIED: 'danger', FAILED: 'danger', CLARIFY: 'warning' }[s] || '')
const scroll = () => nextTick(() => { if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight })

/** 把后端的完整结果摊到消息对象上；表格 = 主结果 + 附表 */
function applyPayload(m, p) {
  Object.assign(m, p, { text: p.answer ?? m.text, pending: false })
  m.tables = [...(p.result?.rows?.length ? [{ title: p.title, result: p.result }] : []), ...(p.extra || [])]
}

async function loadSessions() {
  sessions.value = await copilot.get('/sessions', { silent: true }).catch(() => [])
}

async function openSession(id) {
  sessionId.value = id
  const rows = await copilot.get(`/history/${id}`)
  messages.value = rows.map(r => {
    if (r.role === 'user') return { role: 'user', text: r.text }
    const m = { role: 'assistant', text: r.text }
    applyPayload(m, { ...(r.payload || {}), answer: r.text })
    return m
  })
  scroll()
}

function newSession() {
  sessionId.value = null
  messages.value = []
}

async function ask(q) {
  const text = (q || input.value).trim()
  if (!text || busy.value) return
  input.value = ''
  busy.value = true
  messages.value.push({ role: 'user', text })
  messages.value.push({ role: 'assistant', text: '', pending: true, progress: '理解问题', tables: [] })
  const r = messages.value[messages.value.length - 1]
  scroll()
  try {
    await chatStream({ query: text, session_id: sessionId.value }, (event, data) => {
      if (event === 'ready') sessionId.value = data.session_id
      else if (event === 'progress') r.progress = data.label
      else if (event === 'meta') Object.assign(r, data)
      else if (event === 'table') r.tables.push(data)
      else if (event === 'chart') r.chart = data
      else if (event === 'sql') Object.assign(r, data)
      else if (event === 'degraded') r.degraded = data.notes
      else if (event === 'delta') r.text += data.delta
      else if (event === 'final') applyPayload(r, data)
      else if (event === 'error') { r.text = data.error; r.status = 'FAILED' }
      scroll()
    })
  } catch (e) {
    r.text = e.message || '请求失败'
    r.status = 'FAILED'
  } finally {
    r.pending = false
    busy.value = false
    loadSessions()
  }
}

/** 导出：先拿预览，用户确认后才生成文件 */
async function exportResult(m) {
  const { export_id, preview } = await copilot.post('/export', { result_id: m.result_id, audit_id: m.audit_id })
  const lines = [
    h('p', `将导出「${preview.title}」，共 ${preview.rows} 行、${preview.columns.length} 列。`),
    preview.sensitive.length ? h('p', { style: 'color:#c45656' }, `包含敏感字段：${preview.sensitive.join('、')}`) : null,
    preview.masked.length ? h('p', `已打码字段：${preview.masked.join('、')}`) : null,
    preview.scope_notes.length ? h('p', `数据范围：${preview.scope_notes.join('；')}`) : null,
    h('p', { class: 'muted' }, '文件会带上导出人和导出时间，这次导出会记入审计日志。')
  ]
  let decision = 'confirm'
  try {
    await ElMessageBox({ title: '确认导出', message: h('div', lines), showCancelButton: true, confirmButtonText: '确认导出', cancelButtonText: '取消' })
  } catch {
    decision = 'cancel'
  }
  const res = await copilot.post(`/export/${export_id}/confirm`, { decision })
  if (!res.confirmed) return
  const blob = await copilot.get(`/export/${export_id}/download`, { responseType: 'blob' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = res.file_name
  a.click()
  ElMessage.success('已导出')
}

async function rate(m, rating) {
  let comment = null
  if (rating < 0) {
    const r = await ElMessageBox.prompt('哪里不对？（可以不填）', '反馈', { confirmButtonText: '提交', cancelButtonText: '取消', inputPlaceholder: '比如：数字不对 / 没理解我的问题 / 口径不是我要的' }).catch(() => null)
    if (!r) return
    comment = r.value
  }
  await copilot.post('/feedback', { audit_id: m.audit_id, rating, comment })
  m.rating = rating
  ElMessage.success(rating > 0 ? '谢谢反馈' : '已记录，会用来改进')
}

onMounted(() => {
  loadSessions()
  if (route.query.q) ask(String(route.query.q))      // 从看板点"问一问"带过来的问题
})
</script>

<style scoped>
.copilot { display: flex; height: calc(100vh - 90px); }
.sessions { width: 220px; border-right: 1px solid #e5e8ef; padding: 12px; overflow-y: auto; background: #fff; box-sizing: border-box; }
.session { padding: 8px; border-radius: 6px; cursor: pointer; font-size: 13px; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.session:hover, .session.active { background: #eef3fa; }
.chat { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.messages { flex: 1; overflow-y: auto; padding: 16px 24px; }
.empty { max-width: 760px; margin: 40px auto; text-align: center; }
.ex-group { margin-top: 14px; }
.ex-title { font-size: 12px; color: #8a8f99; margin-bottom: 4px; }
.example { margin: 4px; cursor: pointer; }
.msg { display: flex; margin-bottom: 14px; }
.msg.user { justify-content: flex-end; }
.bubble { max-width: 86%; padding: 10px 14px; border-radius: 10px; background: #fff; line-height: 1.7; font-size: 14px; }
.msg.user .bubble { background: #3a6ea5; color: #fff; }
.msg.assistant .bubble { width: 86%; box-sizing: border-box; }
.tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.progress { margin-top: 6px; color: #8a8f99; font-size: 12px; display: flex; align-items: center; gap: 6px; }
.options { margin-top: 6px; }
.sources { margin-top: 10px; border-top: 1px dashed #e5e8ef; padding-top: 6px; font-size: 12px; }
.source { margin: 4px 0; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.detail { margin-top: 10px; border-bottom: none; }
.defs { margin-bottom: 8px; font-size: 13px; }
.defs ul { margin: 2px 0 0 18px; padding: 0; }
.sql { background: #f6f8fa; border-radius: 6px; padding: 8px 10px; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 4px 0 0; }
.actions { margin-top: 10px; display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.cost { margin-left: auto; font-size: 12px; }
.trace { font-size: 12px; color: #3a6ea5; margin-left: 8px; }
.input { border-top: 1px solid #e5e8ef; padding: 12px 24px; background: #fff; }
.send { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; font-size: 12px; }
:deep(.note) { background: #fdf6ec; color: #8a6d3b; border-left: 3px solid #e6a23c; padding: 2px 8px; margin: 2px 0 6px; font-size: 12px; border-radius: 2px; }
.degraded { background: #fdf6ec; color: #8a6d3b; border-left: 3px solid #e6a23c; padding: 2px 8px; margin: 6px 0; font-size: 12px; border-radius: 2px; }
:deep(.cite) { color: #3a6ea5; font-size: 11px; }
:deep(li) { margin-left: 16px; list-style: disc; }
:deep(code) { background: #f2f4f7; padding: 0 4px; border-radius: 3px; }
:deep(.el-collapse-item__header) { height: 32px; font-size: 12px; color: #8a8f99; }
:deep(.el-collapse-item__content) { padding-bottom: 8px; }
</style>
