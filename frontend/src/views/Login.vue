<template>
  <div class="login-page">
    <div class="top">
      <div class="logo" @click="router.push('/home')"><span class="mark">K</span><b>科朗数智运营平台</b></div>
      <a class="back" @click="router.push('/home')"><el-icon><Back /></el-icon>返回官网</a>
    </div>
    <div class="main">
      <div class="intro">
        <h1>科朗数智运营平台</h1>
        <p class="en">KELI DIGITAL OPERATION PLATFORM</p>
        <ul>
          <li><el-icon><Box /></el-icon>供应链 · 生产制造 · 质量 · 财务 · 人力 一体化</li>
          <li><el-icon><MagicStick /></el-icon>智能问数、企业知识库、跟单工作台</li>
          <li><el-icon><Lock /></el-icon>统一身份认证，按部门和职级控制数据权限</li>
        </ul>
      </div>
      <div class="card">
        <el-tabs v-model="tab" stretch>
          <el-tab-pane label="账号登录" name="pwd">
            <el-form @submit.prevent="submit" class="form">
              <el-form-item>
                <el-input v-model="username" size="large" placeholder="用户名" :prefix-icon="User" autocomplete="username" />
              </el-form-item>
              <el-form-item>
                <el-input v-model="password" size="large" type="password" show-password placeholder="密码" :prefix-icon="Lock" autocomplete="current-password" />
              </el-form-item>
              <div class="row"><el-checkbox v-model="remember">记住用户名</el-checkbox><span class="muted">忘记密码请联系 IT 服务台</span></div>
              <el-button type="primary" size="large" native-type="submit" :loading="loading" class="submit">登 录</el-button>
            </el-form>
          </el-tab-pane>
          <el-tab-pane label="公司 SSO" name="sso">
            <div class="sso">
              <el-icon class="sso-icon"><Key /></el-icon>
              <p>使用公司统一身份账号登录企业知识库。<br />按部门和职级决定可查看的资料。</p>
              <el-button type="primary" size="large" class="submit" @click="ssoLogin">前往统一身份认证</el-button>
            </div>
          </el-tab-pane>
        </el-tabs>
        <div class="notice"><el-icon><InfoFilled /></el-icon>本系统仅限公司内网访问，远程办公请使用公司电脑连接 VPN。</div>
      </div>
    </div>
    <div class="foot">© 科朗电机集团 · 信息技术部　演示环境</div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Back, Box, InfoFilled, Key, Lock, MagicStick, User } from '@element-plus/icons-vue'
import { login, ssoLogin } from '../api/auth'

const router = useRouter()
const route = useRoute()
const tab = ref(route.query.sso ? 'sso' : 'pwd')
const username = ref(localStorage.getItem('remember_user') || '')
const password = ref('')
const remember = ref(!!localStorage.getItem('remember_user'))
const loading = ref(false)

async function submit() {
  loading.value = true
  try {
    await login(username.value, password.value)
    if (remember.value) localStorage.setItem('remember_user', username.value)
    else localStorage.removeItem('remember_user')
    router.push('/')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page { height: 100%; display: flex; flex-direction: column; background: linear-gradient(120deg, #0a2b66 0%, #0d47a1 55%, #1976d2 100%); color: #fff; position: relative; overflow: hidden; }
.login-page::before { content: ''; position: absolute; right: -200px; top: -200px; width: 700px; height: 700px; border-radius: 50%; background: rgba(255, 255, 255, .05); }
.login-page::after { content: ''; position: absolute; left: -150px; bottom: -250px; width: 600px; height: 600px; border-radius: 50%; background: rgba(255, 255, 255, .04); }
.top { display: flex; align-items: center; justify-content: space-between; padding: 18px 40px; position: relative; z-index: 1; }
.logo { display: flex; align-items: center; gap: 10px; cursor: pointer; font-size: 18px; letter-spacing: 1px; }
.mark { width: 34px; height: 34px; border-radius: 7px; background: #fff; color: #0d47a1; font-weight: 800; display: flex; align-items: center; justify-content: center; font-size: 20px; }
.back { color: #cfe0ff; cursor: pointer; display: flex; align-items: center; gap: 4px; font-size: 14px; }
.back:hover { color: #fff; }
.main { flex: 1; display: flex; align-items: center; justify-content: center; gap: 80px; padding: 0 40px; position: relative; z-index: 1; }
.intro h1 { font-size: 36px; margin: 0; letter-spacing: 3px; white-space: nowrap; }
.en { color: #9fc2ff; letter-spacing: 3px; font-size: 13px; margin: 8px 0 30px; }
.intro ul { list-style: none; padding: 0; margin: 0; }
.intro li { display: flex; align-items: center; gap: 10px; margin: 14px 0; color: #dbe7ff; font-size: 15px; }
.card { width: 380px; background: #fff; color: #1f2329; border-radius: 6px; padding: 26px 30px 22px; box-shadow: 0 20px 50px rgba(0, 20, 60, .35); }
.form { margin-top: 10px; }
.row { display: flex; justify-content: space-between; align-items: center; font-size: 12px; margin: -4px 0 16px; }
.submit { width: 100%; letter-spacing: 4px; }
.sso { text-align: center; padding: 10px 0 6px; }
.sso-icon { font-size: 40px; color: #1565c0; }
.sso p { color: #5b6577; font-size: 13px; line-height: 1.8; }
.notice { margin-top: 16px; font-size: 12px; color: #8a6d3b; background: #fdf6ec; border-radius: 4px; padding: 8px 10px; display: flex; gap: 6px; align-items: flex-start; }
.foot { text-align: center; color: #9fb4da; font-size: 12px; padding: 14px; position: relative; z-index: 1; }
@media (max-width: 900px) { .intro { display: none; } }
</style>
