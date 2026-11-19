<template>
  <div class="site">
    <!-- 顶部工具条：大多数企业官网把「员工通道 / OA」放在右上角 -->
    <div class="topbar">
      <div class="wrap topbar-in">
        <span>深交所主板上市公司（虚构）</span>
        <span class="sp" />
        <a href="#about">投资者关系</a>
        <a href="#contact">联系我们</a>
        <a href="#" @click.prevent>English</a>
        <el-dropdown trigger="hover" placement="bottom-end">
          <span class="staff"><el-icon><Lock /></el-icon>员工通道<el-icon><ArrowDown /></el-icon></span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="go('/login')"><el-icon><Monitor /></el-icon>数智运营平台（ERP）</el-dropdown-item>
              <el-dropdown-item @click="go('/login?sso=1')"><el-icon><Collection /></el-icon>企业知识库（SSO）</el-dropdown-item>
              <el-dropdown-item divided disabled>仅限公司内网 / VPN 访问</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <header class="nav" :class="{ solid: scrolled }">
      <div class="wrap nav-in">
        <div class="logo"><span class="mark">K</span><div><b>科朗</b><small>KELI MOTOR</small></div></div>
        <nav>
          <a v-for="n in NAV" :key="n.id" :href="`#${n.id}`">{{ n.text }}</a>
        </nav>
        <el-button type="primary" class="nav-btn" @click="go('/login')">员工登录</el-button>
      </div>
    </header>

    <section class="hero">
      <div class="wrap hero-in">
        <div class="hero-text">
          <p class="eyebrow">专注微特电机与智能驱控 · 三十余年</p>
          <h1>让每一台设备<br />转得更稳、更静、更省</h1>
          <p class="lead">科朗电机集团研发、生产和销售电机与智能驱控产品，产品覆盖智能家居、健康护理和运动控制三大领域，
            服务全球知名家电与自动化客户。</p>
          <div class="hero-btns">
            <el-button type="primary" size="large" @click="scrollTo('products')">了解产品</el-button>
            <el-button size="large" class="ghost" @click="scrollTo('about')">走进科朗</el-button>
          </div>
        </div>
        <svg class="hero-art" viewBox="0 0 320 320" aria-hidden="true">
          <defs>
            <linearGradient id="g1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7fb2ff" /><stop offset="1" stop-color="#1d5fd0" /></linearGradient>
            <linearGradient id="g2" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e9f1ff" /><stop offset="1" stop-color="#a9c6f5" /></linearGradient>
          </defs>
          <rect x="70" y="90" width="150" height="140" rx="18" fill="url(#g1)" />
          <rect x="60" y="80" width="40" height="160" rx="10" fill="#0f3f8f" />
          <rect x="220" y="140" width="70" height="40" rx="8" fill="url(#g2)" />
          <rect x="285" y="150" width="20" height="20" rx="4" fill="#c8d9f7" />
          <g fill="none" stroke="#cfe0ff" stroke-width="3" opacity=".7">
            <line x1="110" y1="110" x2="110" y2="210" /><line x1="135" y1="110" x2="135" y2="210" />
            <line x1="160" y1="110" x2="160" y2="210" /><line x1="185" y1="110" x2="185" y2="210" />
          </g>
          <circle cx="80" cy="160" r="22" fill="#2b6fe0" stroke="#e9f1ff" stroke-width="4" />
          <circle class="spin" cx="80" cy="160" r="9" fill="#e9f1ff" />
        </svg>
      </div>
    </section>

    <section class="stats">
      <div class="wrap stats-in">
        <div v-for="s in STATS" :key="s.label"><b>{{ s.value }}</b><span>{{ s.label }}</span></div>
      </div>
    </section>

    <section id="products" class="block">
      <div class="wrap">
        <h2>产品中心</h2>
        <p class="sub">七大产品系列，从家电风机到工业伺服</p>
        <div class="cards">
          <div v-for="p in PRODUCTS" :key="p.name" class="card">
            <div class="card-icon"><el-icon><component :is="p.icon" /></el-icon></div>
            <h3>{{ p.name }}</h3>
            <p>{{ p.desc }}</p>
          </div>
        </div>
      </div>
    </section>

    <section id="fields" class="block alt">
      <div class="wrap">
        <h2>应用领域</h2>
        <p class="sub">智能家居 · 健康与护理 · 运动控制</p>
        <div class="fields">
          <span v-for="f in FIELDS" :key="f">{{ f }}</span>
        </div>
      </div>
    </section>

    <section id="about" class="block">
      <div class="wrap about">
        <div>
          <h2 style="text-align: left">关于科朗</h2>
          <p>科朗电机集团股份有限公司是国内主要的微特电机研发制造企业之一，2017 年在深圳证券交易所上市（股票代码 000000）。
            公司拥有三十余年电机及智能驱控研发制造经验，主营电机与智能驱控产品的开发、生产和销售。</p>
          <p>公司是国家高新技术企业、国家专精特新「小巨人」企业、省级企业技术中心和海关 AEO 高级认证企业，
            已成为多家世界 500 强客户的重要供应商。</p>
        </div>
        <ul class="honors">
          <li v-for="h in HONORS" :key="h"><el-icon><Medal /></el-icon>{{ h }}</li>
        </ul>
      </div>
    </section>

    <footer id="contact" class="foot">
      <div class="wrap foot-in">
        <div>
          <div class="logo light"><span class="mark">K</span><div><b>科朗电机集团</b><small>KELI MOTOR GROUP CO., LTD.</small></div></div>
          <p>总部：深圳市南山区深圳湾科技生态园</p>
          <p>生产基地：湖南省祁阳市</p>
        </div>
        <div class="foot-links">
          <b>快速入口</b>
          <a href="#products">产品中心</a><a href="#about">关于我们</a>
          <a href="#" @click.prevent="go('/login')">员工通道 · 数智运营平台</a>
        </div>
      </div>
      <div class="wrap copy">公开信息来自公司官网与年度报告。本页面为内部系统演示用的入口页，不是公司官方网站。</div>
    </footer>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowDown, Collection, Lock, Medal, Monitor } from '@element-plus/icons-vue'

const router = useRouter()
const scrolled = ref(false)
const NAV = [{ id: 'products', text: '产品中心' }, { id: 'fields', text: '应用领域' }, { id: 'about', text: '关于我们' }, { id: 'contact', text: '联系我们' }]
const STATS = [{ value: '30+', label: '年电机研发制造经验' }, { value: '7', label: '大产品系列' }, { value: '2017', label: '深交所上市' }, { value: '500 强', label: '全球客户供应商' }]
const PRODUCTS = [
  { name: '单相罩极异步电机', desc: '家电风机、排水泵等场景，产销量全球领先', icon: 'WindPower' },
  { name: '直流无刷电机', desc: '扫地机器人、吸尘器、风扇，高效低噪', icon: 'Lightning' },
  { name: '伺服控制系统', desc: '伺服电机与驱动器，工业自动化与机器人', icon: 'Aim' },
  { name: '步进电机', desc: '安防监控、3D 打印、办公自动化', icon: 'Odometer' },
  { name: '贯流风机', desc: '空调、取暖器等家电送风', icon: 'Sunny' },
  { name: '齿轮减速电机', desc: '低速大转矩驱动，智能家居与设备', icon: 'Setting' },
  { name: '外转子电机', desc: '紧凑结构、大转动惯量的风机驱动', icon: 'Refresh' }
]
const FIELDS = ['家用电器', '智能家居', '扫地机器人', '医疗器械', '安防监控', '3D 打印', '机器人', '锂电池设备', '电子制造设备', '数控机床', '办公自动化', '工业自动化']
const HONORS = ['国家高新技术企业', '国家专精特新「小巨人」企业', '省级企业技术中心', '海关 AEO 高级认证企业', '湖南名牌产品']

const go = path => router.push(path)
const scrollTo = id => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
const onScroll = () => { scrolled.value = window.scrollY > 40 }
onMounted(() => window.addEventListener('scroll', onScroll))
onBeforeUnmount(() => window.removeEventListener('scroll', onScroll))
</script>

<style scoped>
.site { min-height: 100%; background: #fff; color: #1d2433; overflow-y: auto; height: 100%; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 0 24px; box-sizing: border-box; }
.topbar { background: #0b2a5b; color: #aebbd3; font-size: 12px; }
.topbar-in { display: flex; align-items: center; gap: 18px; height: 34px; }
.topbar a { color: #aebbd3; text-decoration: none; }
.topbar a:hover { color: #fff; }
.sp { flex: 1; }
.staff { color: #fff; background: #1d5fd0; padding: 5px 12px; border-radius: 3px; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; font-size: 12px; outline: none; }
.nav { position: sticky; top: 0; z-index: 10; background: rgba(255, 255, 255, .92); backdrop-filter: blur(6px); border-bottom: 1px solid #eef1f6; transition: box-shadow .2s; }
.nav.solid { box-shadow: 0 2px 12px rgba(15, 40, 90, .08); }
.nav-in { display: flex; align-items: center; height: 68px; gap: 36px; }
.logo { display: flex; align-items: center; gap: 10px; }
.logo .mark { width: 38px; height: 38px; border-radius: 8px; background: linear-gradient(135deg, #2b74e8, #0b3d91); color: #fff; font-weight: 800; font-size: 22px; display: flex; align-items: center; justify-content: center; }
.logo b { display: block; font-size: 20px; letter-spacing: 2px; color: #0b2a5b; }
.logo small { display: block; font-size: 10px; letter-spacing: 2px; color: #7a8599; }
.logo.light b { color: #fff; }
.nav nav { display: flex; gap: 30px; flex: 1; }
.nav nav a { color: #1d2433; text-decoration: none; font-size: 15px; }
.nav nav a:hover { color: #1d5fd0; }
.hero { background: radial-gradient(1200px 500px at 80% 20%, #2a6ee0 0%, #0b3d91 55%, #0a2b66 100%); color: #fff; }
.hero-in { display: flex; align-items: center; gap: 40px; padding: 80px 24px 90px; }
.hero-text { flex: 1; }
.eyebrow { color: #9fc2ff; letter-spacing: 2px; font-size: 14px; margin: 0 0 14px; }
.hero h1 { font-size: 44px; line-height: 1.25; margin: 0 0 18px; font-weight: 700; }
.lead { color: #d3e2ff; font-size: 16px; line-height: 1.8; max-width: 560px; }
.hero-btns { margin-top: 28px; display: flex; gap: 12px; }
.ghost { background: transparent; color: #fff; border-color: rgba(255, 255, 255, .6); }
.hero-art { width: 340px; height: 340px; flex-shrink: 0; }
.spin { transform-origin: 80px 160px; animation: spin 3s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.stats { background: #f4f7fc; }
.stats-in { display: grid; grid-template-columns: repeat(4, 1fr); padding: 28px 24px; text-align: center; }
.stats b { display: block; font-size: 32px; color: #0b3d91; }
.stats span { color: #5b6577; font-size: 13px; }
.block { padding: 70px 0; }
.block.alt { background: #f8fafd; }
.block h2 { text-align: center; font-size: 30px; margin: 0 0 8px; color: #0b2a5b; }
.sub { text-align: center; color: #6b7487; margin: 0 0 36px; }
.cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; }
.card { border: 1px solid #e8edf5; border-radius: 10px; padding: 22px; transition: all .2s; background: #fff; }
.card:hover { transform: translateY(-4px); box-shadow: 0 10px 24px rgba(15, 50, 120, .1); border-color: #c9dbfa; }
.card-icon { width: 44px; height: 44px; border-radius: 10px; background: #eaf2ff; color: #1d5fd0; display: flex; align-items: center; justify-content: center; font-size: 22px; }
.card h3 { font-size: 16px; margin: 14px 0 6px; }
.card p { color: #6b7487; font-size: 13px; line-height: 1.7; margin: 0; }
.fields { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; }
.fields span { padding: 10px 20px; border-radius: 22px; background: #fff; border: 1px solid #dfe7f3; color: #2c3a55; }
.about { display: grid; grid-template-columns: 3fr 2fr; gap: 50px; align-items: center; }
.about p { color: #4a5568; line-height: 1.9; }
.honors { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
.honors li { background: #f4f7fc; border-left: 3px solid #1d5fd0; padding: 12px 16px; display: flex; align-items: center; gap: 8px; color: #23324f; }
.foot { background: #0a2350; color: #aebbd3; padding-top: 40px; }
.foot-in { display: flex; justify-content: space-between; gap: 40px; padding-bottom: 28px; font-size: 13px; }
.foot-links { display: flex; flex-direction: column; gap: 8px; }
.foot-links b { color: #fff; }
.foot-links a { color: #aebbd3; text-decoration: none; }
.copy { border-top: 1px solid rgba(255, 255, 255, .1); padding: 14px 24px; font-size: 12px; color: #7f8db0; }
@media (max-width: 900px) {
  .hero-art, .nav nav { display: none; }
  .cards { grid-template-columns: repeat(2, 1fr); }
  .stats-in { grid-template-columns: repeat(2, 1fr); gap: 16px; }
  .about { grid-template-columns: 1fr; }
  .hero h1 { font-size: 32px; }
}
</style>
