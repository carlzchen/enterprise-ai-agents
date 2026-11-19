import { createRouter, createWebHistory } from 'vue-router'
import { acceptSsoToken, auth, hasRole, isSso } from './api/auth'
import { MENU } from './menu'

const pageRoutes = MENU.flatMap(g => g.items).map(i => ({
  path: i.path,
  component: i.component,
  props: i.config ? { config: i.config } : {},
  meta: { title: i.title, roles: i.roles }
}))

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/home', component: () => import('./views/Homepage.vue'), meta: { public: true } },
    { path: '/login', component: () => import('./views/Login.vue'), meta: { public: true } },
    {
      path: '/',
      component: () => import('./views/Layout.vue'),
      children: [
        { path: '', redirect: () => (isSso() ? '/kbase' : '/workbench') },
        { path: 'workbench', component: () => import('./views/Workbench.vue'), meta: { title: '我的工作台' } },
        ...pageRoutes
      ]
    }
  ]
})

router.beforeEach(to => {
  if (to.hash.startsWith('#token=') && acceptSsoToken(to.hash)) return { path: to.path, hash: '' }      // SSO 登录回跳
  if (to.meta.public) return to.path === '/login' && auth.user && localStorage.getItem('token') ? '/' : true
  if (!localStorage.getItem('token')) return (to.redirectedFrom?.path ?? to.path) === '/' ? '/home' : '/login'   // 没登录：打开网站先到官网，直接访问内部页面到登录页
  if (isSso() && !to.path.startsWith('/kbase')) return '/kbase'          // SSO 账号只能用知识库
  if (to.meta.roles && !hasRole(to.meta.roles)) return '/'
})

export default router
