<template>
  <div class="kbase">
    <aside class="sessions">
      <el-button type="primary" style="width: 100%" @click="newSession">新对话</el-button>
      <div v-for="s in sessions" :key="s.session_id" class="session" :class="{ active: s.session_id === sessionId }" @click="openSession(s.session_id)">
        {{ s.title }}
      </div>
    </aside>
    <section class="chat">
      <div ref="scroller" class="messages">
        <div v-if="!messages.length" class="empty">
          <h3>企业知识库问答</h3>
          <p class="muted">查参数、查图纸尺寸、查工艺和故障处理。参数直接取结构化数据，说明类内容来自文档原文；每句话都标出处，没有依据就不回答。</p>
          <div v-for="g in examples" :key="g.title" class="ex-group">
            <div class="ex-title">{{ g.title }}</div>
            <el-tag v-for="q in g.items" :key="q" class="example" effect="plain" @click="ask(q)">{{ q }}</el-tag>
          </div>
        </div>
        <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
          <div class="bubble">
            <div v-if="m.role === 'user'">{{ m.text }}</div>
            <template v-else>
              <div v-if="m.route || m.status" class="tags">
                <el-tag v-if="m.route" size="small">{{ ROUTES[m.route] || m.route }}</el-tag>
                <el-tag v-for="x in m.models || []" :key="x" size="small" type="info" effect="plain">{{ x }}</el-tag>
                <el-tag v-if="STATUS[m.status]" size="small" :type="STATUS[m.status].type" effect="plain">{{ STATUS[m.status].text }}</el-tag>
                <el-tag v-if="subjectText(m)" size="small" type="warning" effect="light">{{ subjectText(m) }}</el-tag>
                <el-tag v-if="m.rewritten" size="small" type="info" effect="plain">理解为：{{ m.rewritten }}</el-tag>
                <el-tag v-if="m.cache_hit" size="small" type="success" effect="plain">缓存</el-tag>
              </div>
              <div v-if="m.text" class="md" v-html="render(m.text)" />
              <div v-if="m.status === 'CLARIFY' && m.subject?.candidates?.length" class="pick">
                <span class="muted">选一个型号重新问：</span>
                <el-tag v-for="c in m.subject.candidates" :key="c" class="example" effect="plain" @click="ask(withModel(m.question, c))">{{ c }}</el-tag>
              </div>
              <div v-if="m.pending" class="progress">
                <el-icon class="is-loading"><Loading /></el-icon><span>{{ m.progress }}…</span>
              </div>
              <div v-if="m.verify_note" class="note">{{ m.verify_note }}</div>
              <div v-for="d in m.degraded || []" :key="d" class="note">已降级：{{ d }}</div>
              <div v-if="m.sources?.length" class="sources">
                <div class="muted">依据的资料（点开看原文片段）</div>
                <div v-for="s in m.sources" :key="s.index">
                  <div class="source" @click="s.open = !s.open">
                    <el-tag size="small">[{{ s.index }}]</el-tag>
                    <el-tag size="small" type="info" effect="plain">{{ KINDS[s.type] || '资料' }}</el-tag>
                    <span>{{ s.title }}<template v-if="s.section"> · {{ s.section }}</template></span>
                    <span class="muted"><template v-if="s.doc_key">{{ s.doc_key }}</template><template v-if="s.version"> · 版本 {{ s.version }}</template><template v-if="s.page"> · 第 {{ s.page }} 页</template></span>
                    <span v-if="s.score != null" class="muted">相关度 {{ s.score.toFixed(2) }}</span>
                    <span v-if="s.routes" class="muted">召回：{{ routeText(s.routes) }}</span>
                    <span v-if="s.images?.length" class="muted">附图 {{ s.images.length }} 张</span>
                  </div>
                  <div v-if="s.images?.length" class="imgs">
                    <el-image v-for="im in s.images" :key="im.sha" :src="imgUrl(im)" :preview-src-list="s.images.map(imgUrl)" fit="contain"
                              class="thumb" :title="im.summary || '文档附图'" lazy preview-teleported>
                      <template #error><div class="img-err">图片加载失败</div></template>
                    </el-image>
                  </div>
                  <pre v-if="s.open" class="snippet">{{ s.snippet }}</pre>
                </div>
              </div>
              <div v-if="!m.pending && m.answer_id" class="actions">
                <el-button size="small" :type="m.rating === 1 ? 'success' : ''" :icon="CircleCheck" @click="rate(m, 1)">有用</el-button>
                <el-button size="small" :type="m.rating === -1 ? 'danger' : ''" :icon="CircleClose" @click="openBad(m)">不对</el-button>
                <a v-if="m.trace_url && isAdmin" :href="m.trace_url" target="_blank" class="trace">查看链路</a>
                <span class="muted cost" v-if="m.elapsed_ms">{{ (m.elapsed_ms / 1000).toFixed(1) }} 秒<template v-if="m.usage?.total"> · {{ m.usage.total }} tokens</template></span>
              </div>
            </template>
          </div>
        </div>
      </div>
      <div class="input">
        <el-input v-model="input" type="textarea" :rows="2" resize="none" :disabled="busy"
                  placeholder="例如：KL-SV-60-200W 的额定转矩是多少？ 伺服电机过热怎么排查？（Enter 发送，Shift+Enter 换行）"
                  @keydown.enter.exact.prevent="ask()" />
        <div class="send">
          <span class="muted">
            <template v-if="me">当前身份：{{ me.name || me.user_id }}<template v-if="me.dept_path"> · {{ me.dept_path }}</template> · 可看到「{{ me.clearance }}」及以下密级、
              有权限的资料</template>
            <template v-else>只能查到你有权限看的资料</template>
            <span v-for="n in me?.notes || []" :key="n" class="warn">（{{ n }}）</span>
          </span>
          <el-button type="primary" :loading="busy" @click="ask()">发送</el-button>
        </div>
      </div>
    </section>
    <el-dialog v-model="bad.show" title="这个回答哪里不对" width="480px">
      <el-form label-width="90px">
        <el-form-item label="问题类型">
          <el-radio-group v-model="bad.reason">
            <el-radio v-for="r in REASONS" :key="r" :value="r">{{ r }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="正确答案"><el-input v-model="bad.correct" type="textarea" :rows="3" placeholder="知道的话请填上，复盘时会用到（可以不填）" /></el-form-item>
        <el-form-item label="补充说明"><el-input v-model="bad.comment" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="bad.show = false">取消</el-button>
        <el-button type="primary" @click="submitBad">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { auth } from '../api/auth'
import { ElMessage } from 'element-plus'
import { CircleCheck, CircleClose, Loading } from '@element-plus/icons-vue'
import kbase, { askStream } from '../api/kbase'
import { renderMarkdown } from '../api/markdown'

const ROUTES = { PARAM: '参数查询', DESC: '文档检索', MIXED: '参数 + 文档' }
const KINDS = { sql: '结构化参数', erp: '金蝶 EAS BOM', kg: '知识图谱', kb: '文档' }
const STATUS = {
  DENIED: { text: '无权限', type: 'danger' }, BLOCKED: { text: '已拦截', type: 'danger' }, EMPTY: { text: '没有资料', type: 'warning' },
  LOW: { text: '相关度不足，未作答', type: 'warning' }, DEGRADED: { text: '已降级为摘录原文', type: 'warning' },
  CLARIFY: { text: '需要确认型号', type: 'warning' }
}
const SERIES = { SERVO: '伺服', STEPPER: '步进', BLDC: '无刷直流', SHADED_POLE: '罩极', CROSSFLOW: '贯流', GEAR: '减速' }
const ROUTE_CN = { dense: '语义', hyde: '假设答案', bm25: '关键词', sparse: '稀疏', fulltext: '全文' }
const REASONS = ['答案错误', '引用不对', '没找到资料', '资料过期', '其他']
const examples = [
  { title: '查参数', items: ['KL-SV-60-200W 的额定转矩是多少？', 'KL-SV-80-400W 和 KL-SV-80-750W 的额定电流对比', 'KL-SV-80-400W 的轴径是多少？'] },
  { title: '查文档', items: ['伺服电机过热报警怎么排查？', 'AL.060 是什么报警，怎么处理？', '转子动平衡的工艺要求是什么？'] },
  { title: '参数 + 说明', items: ['KL-SV-60-200W 的额定转速是多少，选型时要注意什么？'] },
  { title: '查物料和关联关系', items: ['KL-SV-80-400W 用了哪些物料？', '磁钢用在哪些产品上？', 'KL-SV-80-400W 和 KL-SV-80-750W 共用哪些物料？'] }
]

const isAdmin = computed(() => auth.user?.role === 'ADMIN')
const me = ref(null)

/** 本轮锁定的主题：「锁定型号：KL-SV-80-750W（来自上文）」 */
function subjectText(m) {
  const s = m.subject
  if (!s || m.status === 'CLARIFY') return ''
  const from = { 问题: '', 补全: '（自动补全）', 上文: '（沿用上文）', 候选: '（多个候选）' }[s.source] ?? ''
  if (s.models?.length) return `锁定型号：${s.models.join('、')}${from}`
  if (s.series) return `锁定系列：${SERIES[s.series] || s.series}${from}`
  return ''
}
const withModel = (q, model) => (/KL-[A-Za-z]{2}-[0-9A-Za-z-]+/i.test(q || '') ? q.replace(/KL-[A-Za-z]{2}-[0-9A-Za-z-]+/i, model) : `${model} ${q || ''}`)
const routeText = r => Object.entries(r).map(([k, v]) => `${ROUTE_CN[k] || k}第${v}`).join('、')
/** 图片走知识库服务鉴权后跳转到临时签名链接；<img> 带不了请求头，凭证放在查询参数里 */
const imgUrl = im => `/kbase-api${im.path}?token=${encodeURIComponent(localStorage.getItem('token') || '')}`
const sessions = ref([])
const sessionId = ref(null)
const messages = ref([])
const input = ref('')
const busy = ref(false)
const scroller = ref()
const bad = reactive({ show: false, m: null, reason: '答案错误', correct: '', comment: '' })

const render = text => renderMarkdown(text).replace(/\[(\d+)\]/g, '<sup class="cite">[$1]</sup>')
const scroll = () => nextTick(() => { if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight })

async function loadSessions() {
  sessions.value = await kbase.get('/sessions', { silent: true }).catch(() => [])
}

async function openSession(id) {
  sessionId.value = id
  const rows = await kbase.get(`/history/${id}`)
  messages.value = rows.map(r => (r.role === 'user' ? { role: 'user', text: r.text } : { role: 'assistant', text: r.text, ...(r.payload || {}) }))
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
  messages.value.push({ role: 'assistant', text: '', pending: true, progress: '理解问题', question: text })
  const r = messages.value[messages.value.length - 1]
  scroll()
  try {
    await askStream({ query: text, session_id: sessionId.value }, (event, data) => {
      if (event === 'ready') sessionId.value = data.session_id
      else if (event === 'progress') r.progress = data.label
      else if (event === 'meta') Object.assign(r, data)
      else if (event === 'sources') r.sources = data.sources
      else if (event === 'degraded') r.degraded = data.notes
      else if (event === 'delta') r.text += data.delta
      else if (event === 'replace') r.text = data.answer
      else if (event === 'final') Object.assign(r, data, { text: data.answer ?? r.text, pending: false })
      else if (event === 'error') { r.text = data.error; r.status = 'FAILED' }
      scroll()
    })
  } catch (e) {
    r.text = e.message || '请求失败'
  } finally {
    r.pending = false
    busy.value = false
    loadSessions()
  }
}

async function rate(m, rating) {
  await kbase.post('/feedback', { answer_id: m.answer_id, rating })
  m.rating = rating
  ElMessage.success('谢谢反馈')
}

function openBad(m) {
  Object.assign(bad, { show: true, m, reason: '答案错误', correct: '', comment: '' })
}

async function submitBad() {
  await kbase.post('/feedback', { answer_id: bad.m.answer_id, rating: -1, reason: bad.reason, correct_answer: bad.correct || null, comment: bad.comment || null })
  bad.m.rating = -1
  bad.show = false
  ElMessage.success('已记入 Bad Case 池，会在每周复盘时处理')
}

onMounted(() => {
  loadSessions()
  kbase.get('/auth/me', { silent: true }).then(r => { me.value = r }).catch(() => {})
})
</script>

<style scoped>
.kbase { display: flex; height: calc(100vh - 90px); }
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
.note { background: #fdf6ec; color: #8a6d3b; border-left: 3px solid #e6a23c; padding: 2px 8px; margin: 6px 0; font-size: 12px; border-radius: 2px; }
.sources { margin-top: 10px; border-top: 1px dashed #e5e8ef; padding-top: 6px; font-size: 12px; }
.source { margin: 4px 0; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; cursor: pointer; }
.snippet { background: #f6f8fa; border-radius: 6px; padding: 8px 10px; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 2px 0 6px; font-family: inherit; }
.actions { margin-top: 10px; display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.cost { margin-left: auto; font-size: 12px; }
.imgs { display: flex; gap: 8px; flex-wrap: wrap; margin: 4px 0 6px 28px; }
.thumb { width: 120px; height: 84px; border: 1px solid #e5e8ef; border-radius: 6px; background: #fafbfc; cursor: zoom-in; }
.img-err { font-size: 11px; color: #b0b5bf; display: flex; align-items: center; justify-content: center; height: 100%; }
.pick { margin-top: 6px; }
.warn { color: #b88230; }
.trace { font-size: 12px; color: #3a6ea5; margin-left: 8px; }
.input { border-top: 1px solid #e5e8ef; padding: 12px 24px; background: #fff; }
.send { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; font-size: 12px; }
:deep(.cite) { color: #3a6ea5; font-size: 11px; }
:deep(li) { margin-left: 16px; list-style: disc; }
:deep(code) { background: #f2f4f7; padding: 0 4px; border-radius: 3px; }
</style>
