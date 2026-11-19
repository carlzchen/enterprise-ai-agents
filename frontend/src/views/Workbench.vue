<template>
  <div class="wb">
    <div class="hello">
      <div>
        <h2>{{ greeting }}，{{ auth.user?.displayName || auth.user?.username }}</h2>
        <p>{{ today }}　{{ auth.user?.dept || '' }}　{{ auth.user?.role || '' }}</p>
      </div>
      <div class="hello-stats">
        <div @click="go('/oa/center')"><b>{{ todo.total }}</b><span>待我审批</span></div>
        <div @click="go('/workflow')"><b>{{ apps.length }}</b><span>智能应用</span></div>
        <div @click="go('/oa/notices')"><b>{{ notices.length }}</b><span>最新公告</span></div>
      </div>
    </div>

    <div class="grid">
      <div class="panel ai">
        <div class="panel-title"><el-icon><MagicStick /></el-icon>智能应用<span class="tag">AI</span></div>
        <div class="ai-cards">
          <div v-for="a in apps" :key="a.path" class="ai-card" :style="{ '--c': a.color }" @click="go(a.path)">
            <el-icon class="ai-icon"><component :is="a.icon" /></el-icon>
            <div><b>{{ a.title }}</b><p>{{ a.desc }}</p></div>
          </div>
        </div>
      </div>

      <div class="panel todo">
        <div class="panel-title"><el-icon><Bell /></el-icon>我的待办<a @click="go('/oa/center')">全部</a></div>
        <div v-if="!todo.rows.length" class="empty">{{ auth.user?.employeeId ? '没有待办，干得漂亮' : '当前账号未关联员工档案' }}</div>
        <div v-for="r in todo.rows" :key="r.id || r.instance_id" class="todo-row" @click="go('/oa/center')">
          <el-tag size="small" effect="plain">{{ r.def_name || r.defName || '审批' }}</el-tag>
          <span class="ellipsis">{{ r.title || r.instance_title || '待办事项' }}</span>
          <span class="muted">{{ fmt(r.created_at || r.createdAt) }}</span>
        </div>
      </div>

      <div class="panel fav">
        <div class="panel-title"><el-icon><Star /></el-icon>常用功能</div>
        <div class="tiles">
          <div v-for="t in tiles" :key="t.path" class="tile" @click="go(t.path)">
            <span class="tile-icon" :style="{ background: t.bg }"><el-icon><component :is="t.icon" /></el-icon></span>
            <span>{{ t.title }}</span>
          </div>
        </div>
      </div>

      <div class="panel notice">
        <div class="panel-title"><el-icon><Notification /></el-icon>公司公告<a @click="go('/oa/notices')">更多</a></div>
        <div v-if="!notices.length" class="empty">暂无公告</div>
        <div v-for="n in notices" :key="n.id" class="notice-row" @click="go('/oa/notices')">
          <span class="dot" /><span class="ellipsis">{{ n.title }}</span><span class="muted">{{ fmt(n.published_at || n.publishedAt || n.created_at) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { auth, hasRole, isSso } from '../api/auth'
import http from '../api/http'
import { MENU } from '../menu'

const router = useRouter()
const go = p => router.push(p)
const notices = ref([])
const todo = reactive({ total: 0, rows: [] })
const h = new Date().getHours()
const greeting = h < 11 ? '上午好' : h < 13 ? '中午好' : h < 18 ? '下午好' : '晚上好'
const today = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })
const fmt = v => (v ? String(v).replace('T', ' ').slice(5, 16) : '')

const allowed = computed(() => new Map(MENU.flatMap(g => g.items).filter(i => hasRole(i.roles) && (!isSso() || i.path.startsWith('/kbase'))).map(i => [i.path, i])))
const AI = [
  { path: '/kbase', title: '企业知识库', desc: '查参数、图纸、工艺和故障处理，回答带出处和原图', icon: 'Collection', color: '#1257c4' },
  { path: '/copilot', title: '智能问数', desc: '一句话查经营和人力数据，自动生成图表', icon: 'DataAnalysis', color: '#0e9f6e' },
  { path: '/workflow', title: '跟单工作台', desc: '邮件自动分拣、报价议价、非标评估', icon: 'Message', color: '#d97706' }
]
const apps = computed(() => AI.filter(a => allowed.value.has(a.path)))
const FAV = [
  ['/dashboard', 'DataLine', '#3b82f6'], ['/sales/orders', 'Tickets', '#0ea5e9'], ['/purchase/orders', 'ShoppingCart', '#14b8a6'],
  ['/production/work-orders', 'Cpu', '#8b5cf6'], ['/mes/board', 'Monitor', '#6366f1'], ['/inventory/stock', 'Box', '#f59e0b'],
  ['/quality/iqc', 'Checked', '#ef4444'], ['/quality/complaints', 'Warning', '#f97316'], ['/oa/center', 'Stamp', '#10b981'],
  ['/kbase', 'Collection', '#1257c4'], ['/copilot', 'DataAnalysis', '#0e9f6e'], ['/workflow', 'Message', '#d97706']
]
const tiles = computed(() => FAV.filter(([p]) => allowed.value.has(p)).map(([p, icon, bg]) => ({ path: p, icon, bg, title: allowed.value.get(p).title })))

onMounted(async () => {
  if (isSso()) return                                         // SSO 账号只能用知识库，不调 ERP 接口
  http.get('/oa/notices', { params: { limit: 6 }, silent: true }).then(r => { notices.value = (r.records || r || []).slice(0, 6) }).catch(() => {})
  if (auth.user?.employeeId) {
    http.get('/oa/my/todo', { params: { page: 1, size: 6 }, silent: true }).then(r => { todo.total = r.total; todo.rows = r.records || [] }).catch(() => {})
  }
})
</script>

<style scoped>
.wb { padding: 14px; }
.hello { background: linear-gradient(100deg, #1257c4, #2f7be8); color: #fff; border-radius: 6px; padding: 18px 24px; display: flex; align-items: center; justify-content: space-between; }
.hello h2 { margin: 0 0 6px; font-size: 20px; }
.hello p { margin: 0; color: #d6e4ff; font-size: 13px; }
.hello-stats { display: flex; gap: 34px; }
.hello-stats div { text-align: center; cursor: pointer; }
.hello-stats b { display: block; font-size: 26px; }
.hello-stats span { font-size: 12px; color: #d6e4ff; }
.grid { display: grid; grid-template-columns: 3fr 2fr; gap: 14px; margin-top: 14px; }
.panel { background: #fff; border-radius: 6px; padding: 14px 16px; border: 1px solid #e3e8f0; }
.panel-title { font-weight: 600; display: flex; align-items: center; gap: 6px; margin-bottom: 12px; color: #1f2a3d; }
.panel-title a { margin-left: auto; font-weight: normal; font-size: 12px; color: #1257c4; cursor: pointer; }
.tag { background: linear-gradient(90deg, #7c3aed, #2563eb); color: #fff; font-size: 10px; padding: 1px 6px; border-radius: 8px; }
.ai-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.ai-card { border: 1px solid #e3e8f0; border-radius: 6px; padding: 14px; display: flex; gap: 12px; cursor: pointer; transition: all .15s; border-top: 3px solid var(--c); }
.ai-card:hover { box-shadow: 0 6px 18px rgba(18, 87, 196, .12); transform: translateY(-2px); }
.ai-icon { font-size: 26px; color: var(--c); flex-shrink: 0; }
.ai-card b { font-size: 15px; }
.ai-card p { margin: 6px 0 0; font-size: 12px; color: #6b7487; line-height: 1.6; }
.tiles { display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px 8px; }
.tile { display: flex; flex-direction: column; align-items: center; gap: 6px; cursor: pointer; font-size: 12px; color: #3a4558; text-align: center; }
.tile-icon { width: 42px; height: 42px; border-radius: 10px; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 20px; }
.tile:hover span:last-child { color: #1257c4; }
.todo-row, .notice-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px dashed #edf0f5; font-size: 13px; cursor: pointer; }
.todo-row:hover, .notice-row:hover { color: #1257c4; }
.ellipsis { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: #1257c4; flex-shrink: 0; }
.empty { color: #9aa3b2; font-size: 13px; padding: 20px 0; text-align: center; }
@media (max-width: 1100px) { .grid { grid-template-columns: 1fr; } .tiles { grid-template-columns: repeat(4, 1fr); } }
</style>
