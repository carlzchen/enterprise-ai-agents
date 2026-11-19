<template>
  <div class="shell">
    <!-- 顶栏：深蓝，左边品牌 + 应用中心，中间菜单搜索，右边待办、消息、用户 -->
    <header class="topbar">
      <div class="brand" @click="router.push('/')"><span class="mark">K</span><span>科朗数智运营平台</span></div>
      <div class="app-center" :class="{ open: appOpen }" @click="appOpen = !appOpen"><el-icon><Grid /></el-icon>应用中心<el-icon><ArrowDown /></el-icon></div>
      <el-autocomplete v-model="keyword" class="search" :fetch-suggestions="searchMenu" placeholder="搜索功能，如：销售订单、知识库" :prefix-icon="Search"
                       :trigger-on-focus="false" value-key="label" @select="openItem" clearable />
      <span class="spacer" />
      <el-tooltip content="待办审批" placement="bottom">
        <el-badge :value="auth.user?.pendingApprovals || 0" :hidden="!auth.user?.pendingApprovals" :max="99" class="tool">
          <el-icon @click="router.push('/oa/center')"><Bell /></el-icon>
        </el-badge>
      </el-tooltip>
      <el-tooltip content="回到工作台" placement="bottom"><el-icon class="tool" @click="router.push('/')"><House /></el-icon></el-tooltip>
      <el-dropdown trigger="click" @command="onUser">
        <span class="user">
          <el-avatar :size="28" class="avatar">{{ (auth.user?.displayName || auth.user?.username || '?').slice(0, 1) }}</el-avatar>
          <span class="uname">{{ auth.user?.displayName || auth.user?.username }}</span><el-icon><ArrowDown /></el-icon>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item disabled>{{ auth.user?.dept || '—' }} · {{ auth.user?.role }}</el-dropdown-item>
            <el-dropdown-item command="home" divided><el-icon><Promotion /></el-icon>访问公司官网</el-dropdown-item>
            <el-dropdown-item command="logout"><el-icon><SwitchButton /></el-icon>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </header>

    <!-- 应用中心：所有模块和功能一屏展开 -->
    <transition name="drop">
      <div v-if="appOpen" class="app-panel" @click.self="appOpen = false">
        <div class="app-panel-in">
          <div v-for="g in menu" :key="g.title" class="app-group">
            <div class="app-group-title"><el-icon><component :is="g.icon" /></el-icon>{{ g.title }}</div>
            <a v-for="i in g.items" :key="i.path" @click="openItem({ path: i.path })">{{ i.title }}</a>
          </div>
        </div>
      </div>
    </transition>

    <div class="body">
      <aside class="aside" :class="{ collapsed }">
        <el-menu :default-active="route.path" router :collapse="collapsed" :collapse-transition="false" unique-opened>
          <el-menu-item index="/workbench"><el-icon><HomeFilled /></el-icon><template #title>我的工作台</template></el-menu-item>
          <template v-for="g in menu" :key="g.title">
            <el-menu-item v-if="g.items.length === 1 && g.flat" :index="g.items[0].path">
              <el-icon><component :is="g.icon" /></el-icon><template #title>{{ g.items[0].title }}</template>
            </el-menu-item>
            <el-sub-menu v-else :index="g.title">
              <template #title><el-icon><component :is="g.icon" /></el-icon><span>{{ g.title }}</span></template>
              <el-menu-item v-for="i in g.items" :key="i.path" :index="i.path">{{ i.title }}</el-menu-item>
            </el-sub-menu>
          </template>
        </el-menu>
        <div class="collapse" @click="collapsed = !collapsed"><el-icon><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon></div>
      </aside>
      <section class="content">
        <!-- 多页签：打开过的功能留在上面，点一下切换 -->
        <div class="tabs">
          <div v-for="t in tabs" :key="t.path" class="tab" :class="{ active: t.path === route.path }" @click="router.push(t.path)">
            <span>{{ t.title }}</span>
            <el-icon v-if="t.path !== '/workbench'" class="close" @click.stop="closeTab(t.path)"><Close /></el-icon>
          </div>
          <span class="spacer" />
          <el-dropdown trigger="click" @command="c => (c === 'others' ? closeOthers() : closeAll())">
            <span class="tab-ops"><el-icon><MoreFilled /></el-icon></span>
            <template #dropdown>
              <el-dropdown-menu><el-dropdown-item command="others">关闭其他页签</el-dropdown-item><el-dropdown-item command="all">关闭全部页签</el-dropdown-item></el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
        <main class="view">
          <router-view :key="route.path" />
        </main>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown, Search } from '@element-plus/icons-vue'
import { auth, hasRole, isSso, logout } from '../api/auth'
import http from '../api/http'
import kbase from '../api/kbase'
import { MENU } from '../menu'

const route = useRoute()
const router = useRouter()
const appOpen = ref(false)
const collapsed = ref(false)
const keyword = ref('')

/** 刷新当前用户信息（待办数、部门），切换页面时更新 */
async function refreshMe() {
  if (isSso()) {                                    // SSO 账号的凭证只在知识库有效：身份信息问知识库，不问 ERP
    try {
      const me = await kbase.get('/auth/me', { silent: true })
      auth.user = { ...auth.user, displayName: me.name || auth.user.displayName, dept: me.dept_path, role: (me.roles || [])[0] || auth.user.role }
      localStorage.setItem('user', JSON.stringify(auth.user))
    } catch { /* 401 由拦截器处理 */ }
    return
  }
  try {
    auth.user = await http.get('/auth/me')
    localStorage.setItem('user', JSON.stringify(auth.user))
  } catch { /* 401 由拦截器处理 */ }
}
onMounted(refreshMe)
watch(() => route.path, refreshMe)
const menu = computed(() =>
  MENU.map(g => ({ ...g, items: g.items.filter(i => hasRole(i.roles) && (!isSso() || i.path.startsWith('/kbase'))) })).filter(g => g.items.length)
)
const flat = computed(() => menu.value.flatMap(g => g.items.map(i => ({ label: `${i.title}　·　${g.title}`, path: i.path, title: i.title }))))

function searchMenu(q, cb) {
  const k = (q || '').trim().toLowerCase()
  cb(k ? flat.value.filter(x => x.label.toLowerCase().includes(k)).slice(0, 12) : [])
}
function openItem(x) {
  appOpen.value = false
  keyword.value = ''
  router.push(x.path)
}

// ---------- 多页签 ----------
const TAB_KEY = 'kl_tabs'
const home = { path: '/workbench', title: '我的工作台' }
const tabs = ref((() => { try { return JSON.parse(sessionStorage.getItem(TAB_KEY)) || [home] } catch { return [home] } })())
watch(() => route.path, p => {
  const title = route.meta.title || (p === '/workbench' ? home.title : '')
  if (title && !tabs.value.some(t => t.path === p)) tabs.value.push({ path: p, title })
  if (tabs.value.length > 12) tabs.value.splice(1, 1)
  sessionStorage.setItem(TAB_KEY, JSON.stringify(tabs.value))
}, { immediate: true })
function closeTab(p) {
  const i = tabs.value.findIndex(t => t.path === p)
  tabs.value.splice(i, 1)
  sessionStorage.setItem(TAB_KEY, JSON.stringify(tabs.value))
  if (p === route.path) router.push(tabs.value[Math.max(0, i - 1)].path)
}
function closeOthers() {
  tabs.value = tabs.value.filter(t => t.path === home.path || t.path === route.path)
  sessionStorage.setItem(TAB_KEY, JSON.stringify(tabs.value))
}
function closeAll() {
  tabs.value = [home]
  sessionStorage.setItem(TAB_KEY, JSON.stringify(tabs.value))
  router.push(home.path)
}

function onUser(cmd) {
  if (cmd === 'home') router.push('/home')
  if (cmd === 'logout') {
    logout()
    sessionStorage.removeItem(TAB_KEY)
    router.push('/login')
  }
}
</script>

<style scoped>
.shell { height: 100%; display: flex; flex-direction: column; background: #eef1f6; }
.topbar { height: 52px; flex-shrink: 0; display: flex; align-items: center; gap: 18px; padding: 0 16px; color: #fff;
  background: linear-gradient(90deg, #0b3a8a 0%, #1257c4 60%, #1a6ad8 100%); box-shadow: 0 2px 6px rgba(10, 40, 100, .25); z-index: 20; }
.brand { display: flex; align-items: center; gap: 10px; font-size: 17px; font-weight: 600; letter-spacing: 1px; cursor: pointer; white-space: nowrap; flex-shrink: 0; }
.mark { width: 30px; height: 30px; border-radius: 6px; background: #fff; color: #1257c4; font-weight: 800; display: flex; align-items: center; justify-content: center; }
.app-center { display: flex; align-items: center; gap: 6px; padding: 6px 12px; white-space: nowrap; flex-shrink: 0; border-radius: 4px; cursor: pointer; font-size: 14px; background: rgba(255, 255, 255, .12); }
.app-center:hover, .app-center.open { background: rgba(255, 255, 255, .22); }
.search { width: clamp(160px, 24vw, 320px); }
.search :deep(.el-input__wrapper) { background: rgba(255, 255, 255, .16); box-shadow: none; border-radius: 16px; }
.search :deep(.el-input__inner) { color: #fff; }
.search :deep(.el-input__inner::placeholder) { color: #c9dafa; }
.search :deep(.el-input__prefix), .search :deep(.el-input__suffix) { color: #c9dafa; }
.spacer { flex: 1; }
.tool { font-size: 19px; cursor: pointer; color: #e4edff; }
.tool:hover { color: #fff; }
.user { display: flex; align-items: center; gap: 8px; color: #fff; cursor: pointer; outline: none; flex-shrink: 0; }
.avatar { background: #ffb020; color: #fff; font-weight: 600; }
.uname { font-size: 14px; white-space: nowrap; }
.app-panel { position: fixed; top: 52px; left: 0; right: 0; bottom: 0; background: rgba(10, 25, 60, .25); z-index: 30; }
.app-panel-in { background: #fff; padding: 22px 30px 26px; display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 18px 24px;
  max-height: 72vh; overflow-y: auto; box-shadow: 0 10px 30px rgba(10, 40, 100, .2); }
.app-group-title { font-weight: 600; color: #0b3a8a; display: flex; align-items: center; gap: 6px; padding-bottom: 6px; margin-bottom: 6px; border-bottom: 2px solid #e3ecfb; }
.app-group a { display: block; font-size: 13px; color: #3a4558; padding: 4px 2px; cursor: pointer; }
.app-group a:hover { color: #1257c4; background: #f2f6fd; }
.drop-enter-active, .drop-leave-active { transition: opacity .15s; }
.drop-enter-from, .drop-leave-to { opacity: 0; }
.body { flex: 1; display: flex; min-height: 0; }
.aside { width: 208px; background: #fff; border-right: 1px solid #dfe5ee; display: flex; flex-direction: column; transition: width .2s; }
.aside.collapsed { width: 64px; }
.aside :deep(.el-menu) { border-right: none; flex: 1; overflow-y: auto; overflow-x: hidden; }
.aside :deep(.el-menu-item.is-active) { background: #e8f0fd; color: #1257c4; border-right: 3px solid #1257c4; }
.aside :deep(.el-sub-menu__title), .aside :deep(.el-menu-item) { height: 42px; line-height: 42px; }
.collapse { height: 38px; display: flex; align-items: center; justify-content: center; border-top: 1px solid #eef1f6; color: #7a8599; cursor: pointer; }
.content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.tabs { height: 38px; flex-shrink: 0; background: #fff; border-bottom: 1px solid #dfe5ee; display: flex; align-items: flex-end; gap: 2px; padding: 0 8px; overflow-x: auto; }
.tab { height: 32px; padding: 0 12px; display: flex; align-items: center; gap: 6px; font-size: 13px; color: #4a5568; background: #f2f4f8; border: 1px solid #e1e6ee;
  border-bottom: none; border-radius: 4px 4px 0 0; cursor: pointer; white-space: nowrap; }
.tab.active { background: #fff; color: #1257c4; border-top: 2px solid #1257c4; font-weight: 600; }
.tab .close { font-size: 12px; color: #9aa3b2; border-radius: 50%; }
.tab .close:hover { background: #d9e2f2; color: #1257c4; }
.tab-ops { height: 32px; display: flex; align-items: center; padding: 0 8px; cursor: pointer; color: #7a8599; }
.view { flex: 1; overflow: auto; }
</style>
