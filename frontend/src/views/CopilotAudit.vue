<template>
  <div class="page">
    <div class="page-card">
      <div class="kpis" v-if="stats">
        <div class="kpi"><div class="v">{{ stats.total }}</div><div class="l">累计提问</div></div>
        <div class="kpi"><div class="v">{{ stats.denied || 0 }}</div><div class="l">被拒绝（越权或写操作）</div></div>
        <div class="kpi"><div class="v">{{ stats.failed || 0 }}</div><div class="l">执行失败</div></div>
        <div class="kpi"><div class="v">{{ stats.exported || 0 }}</div><div class="l">导出次数</div></div>
        <div class="kpi"><div class="v">{{ stats.avg_ms ? (stats.avg_ms / 1000).toFixed(1) + ' 秒' : '—' }}</div><div class="l">平均耗时</div></div>
        <div class="kpi"><div class="v">{{ stats.avg_tokens || '—' }}</div><div class="l">平均 Token</div></div>
      </div>
      <el-tabs v-model="tab">
        <el-tab-pane label="查询审计" name="audit">
          <div class="toolbar">
            <el-input v-model="q.username" placeholder="用户名" clearable style="width: 130px" @change="reload" />
            <el-select v-model="q.status" placeholder="状态" clearable style="width: 130px" @change="reload">
              <el-option v-for="s in ['OK', 'DENIED', 'FAILED', 'CLARIFY']" :key="s" :value="s" :label="STATUS[s][0]" />
            </el-select>
            <el-input v-model="q.keyword" placeholder="问题关键词" clearable style="width: 200px" @change="reload" />
            <el-button @click="reload">查询</el-button>
            <span class="muted">每次提问都会记录：谁、问了什么、执行了什么 SQL、返回多少行。记录由服务账号写入，对话无法修改。</span>
          </div>
          <el-table :data="rows" size="small" border>
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="expand">
                  <p v-if="row.reason"><b>原因：</b>{{ row.reason }}</p>
                  <p v-if="row.scope_note"><b>数据范围：</b>{{ row.scope_note }}</p>
                  <p v-if="row.answer"><b>回答：</b>{{ row.answer }}</p>
                  <pre v-if="row.sql_text" class="sql">{{ row.sql_text }}</pre>
                  <p class="muted">追踪号 {{ row.trace_id || '—' }} · 会话 {{ row.session_id }}</p>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间" width="160" />
            <el-table-column prop="username" label="用户" width="90" />
            <el-table-column prop="role" label="角色" width="110" />
            <el-table-column prop="question" label="问题" min-width="260" show-overflow-tooltip />
            <el-table-column prop="intent" label="意图" width="100" />
            <el-table-column label="路由" width="80"><template #default="{ row }">{{ { rule: '规则', llm: '模型', cache: '缓存' }[row.route_by] }}</template></el-table-column>
            <el-table-column prop="template_id" label="模板" width="64" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag size="small" :type="STATUS[row.status]?.[1]">{{ STATUS[row.status]?.[0] || row.status }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="row_count" label="行数" width="64" align="right" />
            <el-table-column label="耗时" width="72" align="right"><template #default="{ row }">{{ row.elapsed_ms != null ? (row.elapsed_ms / 1000).toFixed(1) + 's' : '' }}</template></el-table-column>
            <el-table-column label="Token" width="72" align="right"><template #default="{ row }">{{ (row.tokens_in || 0) + (row.tokens_out || 0) }}</template></el-table-column>
            <el-table-column label="标记" width="110">
              <template #default="{ row }">
                <el-tag v-if="row.cache_hit" size="small" type="success" effect="plain">缓存</el-tag>
                <el-tag v-if="row.repairs" size="small" type="warning" effect="plain">修正{{ row.repairs }}次</el-tag>
                <el-tag v-if="row.exported" size="small" type="danger" effect="plain">已导出</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination class="pager" layout="total, prev, pager, next" :total="total" :page-size="20" v-model:current-page="page" @current-change="load" />
        </el-tab-pane>

        <el-tab-pane label="Bad Case 池" name="bad">
          <div class="toolbar"><span class="muted">用户点"不对"的回答会进到这里。分析原因、修复后勾选"已修复"，并把这道题加入评测集做回归。</span></div>
          <el-table :data="bad" size="small" border>
            <el-table-column prop="created_at" label="时间" width="160" />
            <el-table-column prop="username" label="用户" width="90" />
            <el-table-column prop="question" label="问题" min-width="220" show-overflow-tooltip />
            <el-table-column prop="comment" label="用户说" min-width="160" show-overflow-tooltip />
            <el-table-column prop="intent" label="意图" width="100" />
            <el-table-column prop="template_id" label="模板" width="64" />
            <el-table-column label="原因分类" width="170">
              <template #default="{ row }">
                <el-select v-model="row.cause" size="small" placeholder="未分析" clearable @change="saveBad(row)">
                  <el-option v-for="c in CAUSES" :key="c" :value="c" :label="c" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="已修复" width="80" align="center">
              <template #default="{ row }"><el-checkbox :model-value="!!row.fixed" @change="v => { row.fixed = v ? 1 : 0; saveBad(row) }" /></template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import copilot from '../api/copilot'

const STATUS = { OK: ['成功', 'success'], DENIED: ['已拒绝', 'danger'], FAILED: ['失败', 'warning'], CLARIFY: ['追问', 'info'] }
const CAUSES = ['意图分错', '选错表', '口径用错', '时间理解错', 'SQL 写错', '多轮继承错', '制度没检索到', '回答表述问题', '数据本身问题']
const tab = ref('audit')
const q = reactive({ username: '', status: '', keyword: '' })
const rows = ref([]), total = ref(0), page = ref(1), stats = ref(null), bad = ref([])

async function load() {
  const res = await copilot.get('/audit', { params: { page: page.value, size: 20, username: q.username || undefined, status: q.status || undefined, keyword: q.keyword || undefined } })
  rows.value = res.items
  total.value = res.total
}
const reload = () => { page.value = 1; load() }
const saveBad = row => copilot.put(`/badcases/${row.id}`, { cause: row.cause || null, fixed: !!row.fixed })

onMounted(async () => {
  load()
  stats.value = await copilot.get('/audit/stats')
  bad.value = await copilot.get('/badcases')
})
</script>

<style scoped>
.kpis { display: flex; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.kpi { flex: 1; min-width: 130px; background: #f6f8fb; border-radius: 8px; padding: 12px 14px; }
.kpi .v { font-size: 22px; font-weight: 600; }
.kpi .l { font-size: 12px; color: #8a8f99; margin-top: 2px; }
.expand { padding: 4px 16px; font-size: 13px; }
.sql { background: #f6f8fa; border-radius: 6px; padding: 8px 10px; font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.pager { margin-top: 12px; justify-content: flex-end; }
</style>
