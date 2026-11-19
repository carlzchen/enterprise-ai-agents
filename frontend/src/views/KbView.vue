<template>
  <div class="page">
    <div class="page-card kb">
      <div class="left">
        <el-input v-model="q" placeholder="全文检索（中文）" clearable @keyup.enter="search" @clear="results = null">
          <template #append><el-button @click="search">搜</el-button></template>
        </el-input>
        <el-select v-model="space" style="width: 100%; margin: 8px 0" @change="loadTree">
          <el-option v-for="s in spaces" :key="s.space_key" :label="`${s.name}（${s.page_count}）`" :value="s.space_key" />
        </el-select>
        <div v-if="results" class="results">
          <div class="muted">命中 {{ results.length }} 个页面</div>
          <div v-for="r in results" :key="r.id" class="hit" @click="openPage(r.id)">
            <b>{{ r.title }}</b> <span class="muted">{{ r.space_name }}</span><div class="snip">{{ r.snippet }}</div>
          </div>
        </div>
        <el-tree v-else :data="tree" node-key="id" :props="{ label: 'title', children: 'children' }" highlight-current
                 @node-click="n => openPage(n.id)">
          <template #default="{ data }"><span>{{ data.title }}<el-tag v-if="data.source_type && data.source_type !== 'MANUAL'" size="small" type="info" style="margin-left: 4px">{{ SRC[data.source_type] }}</el-tag></span></template>
        </el-tree>
        <el-button size="small" style="margin-top: 8px" @click="create(null)">新建页面</el-button>
      </div>
      <div class="right">
        <template v-if="page && !editing">
          <div class="crumb muted">{{ page.space_name }} / {{ page.breadcrumb.map(b => b.title).join(' / ') }}</div>
          <div class="meta muted">v{{ page.version }} · {{ page.author_name || '系统' }} · 更新于 {{ fmt(page.updated_at) }} · 浏览 {{ page.view_count }}
            <el-tag v-if="page.source_type && page.source_type !== 'MANUAL'" size="small">自动沉淀：{{ SRC[page.source_type] }} {{ page.source_ref }}</el-tag></div>
          <div class="actions">
            <el-button size="small" type="primary" @click="edit">编辑</el-button>
            <el-button size="small" @click="create(page.id)">新建子页面</el-button>
            <el-button size="small" :disabled="page.versions.length < 2" @click="showHistory = true">版本历史（{{ page.versions.length }}）</el-button>
          </div>
          <div class="md" v-html="renderMarkdown(page.body)" />
        </template>
        <template v-else-if="editing">
          <el-input v-model="form.title" placeholder="标题" style="margin-bottom: 8px" />
          <div class="editor">
            <el-input v-model="form.body" type="textarea" :rows="24" class="src" />
            <div class="md preview" v-html="renderMarkdown(form.body)" />
          </div>
          <div class="actions">
            <el-input v-model="form.changeNote" placeholder="修改说明（进入版本历史）" style="width: 320px" />
            <el-button type="primary" @click="save(true)">发布</el-button><el-button @click="save(false)">存草稿</el-button><el-button @click="editing = false">取消</el-button>
          </div>
        </template>
        <el-empty v-else description="从左侧选择页面，或全文检索" />
      </div>
    </div>
    <el-dialog v-model="showHistory" title="版本历史与对比" width="860px">
      <div class="toolbar">
        <el-select v-model="v1" style="width: 140px"><el-option v-for="v in page?.versions" :key="v.version" :label="'v' + v.version" :value="v.version" /></el-select> →
        <el-select v-model="v2" style="width: 140px"><el-option v-for="v in page?.versions" :key="v.version" :label="'v' + v.version" :value="v.version" /></el-select>
        <el-button @click="diff">对比</el-button>
        <span v-if="d" class="muted">+{{ d.added }} 行 / -{{ d.deleted }} 行</span>
      </div>
      <el-table :data="page?.versions || []" size="small" border max-height="180">
        <el-table-column prop="version" label="版本" width="60" /><el-table-column label="时间" width="150"><template #default="{ row }">{{ fmt(row.saved_at) }}</template></el-table-column>
        <el-table-column prop="author_name" label="作者" width="90" /><el-table-column prop="change_note" label="说明" />
      </el-table>
      <div v-if="d" class="diff">
        <div v-for="(l, i) in d.lines" :key="i" :class="l.op">{{ l.op === 'ADD' ? '+ ' : l.op === 'DEL' ? '- ' : '  ' }}{{ l.text }}</div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api/http'
import { renderMarkdown } from '../api/markdown'

const SRC = { '8D': '8D', ECN: 'ECN', EAM: '设备', ROUTING: '工艺路线', MANUAL: '' }
const spaces = ref([])
const space = ref('QUAL')
const tree = ref([])
const page = ref(null)
const editing = ref(false)
const form = ref({})
const q = ref('')
const results = ref(null)
const showHistory = ref(false)
const v1 = ref(null), v2 = ref(null)
const d = ref(null)
const fmt = v => v ? String(v).replace('T', ' ').slice(0, 16) : ''

async function loadTree() { results.value = null; tree.value = await http.get(`/kb/spaces/${space.value}/tree`) }
async function openPage(id) {
  page.value = await http.get(`/kb/pages/${id}`)
  editing.value = false
  d.value = null
  v2.value = page.value.version
  v1.value = Math.max(1, page.value.version - 1)
}
async function search() { if (q.value.trim().length >= 2) results.value = await http.get('/kb/search', { params: { q: q.value, limit: 30 } }) }
function edit() { form.value = { id: page.value.id, title: page.value.title, body: page.value.body, changeNote: '' }; editing.value = true }
function create(parentId) { form.value = { parentId, title: '', body: '# 新页面\n\n', changeNote: '创建' }; editing.value = true }
async function save(publish) {
  const body = { spaceKey: space.value, parentId: form.value.parentId, title: form.value.title, body: form.value.body, publish, changeNote: form.value.changeNote }
  page.value = form.value.id ? await http.put(`/kb/pages/${form.value.id}`, body) : await http.post('/kb/pages', body)
  editing.value = false
  ElMessage.success(publish ? `已发布 v${page.value.version}` : '已保存草稿')
  loadTree()
}
async function diff() { d.value = await http.get(`/kb/pages/${page.value.id}/diff`, { params: { from: v1.value, to: v2.value } }) }
onMounted(async () => { spaces.value = await http.get('/kb/spaces'); await loadTree() })
</script>

<style scoped>
.kb { display: flex; gap: 16px; }
.left { width: 300px; flex-shrink: 0; border-right: 1px solid #eee; padding-right: 10px; max-height: 80vh; overflow: auto; }
.right { flex: 1; min-width: 0; }
.crumb { font-size: 12px; }
.meta { margin: 4px 0 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.actions { margin: 8px 0; display: flex; gap: 8px; align-items: center; }
.md { line-height: 1.8; }
.md :deep(.md-table) { border-collapse: collapse; margin: 8px 0; }
.md :deep(.md-table th), .md :deep(.md-table td) { border: 1px solid #dcdfe6; padding: 4px 10px; }
.md :deep(.md-code) { background: #f6f8fb; padding: 10px; border-radius: 4px; white-space: pre-wrap; font-size: 12px; }
.md :deep(li) { margin-left: 18px; }
.editor { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.preview { border: 1px solid #eee; padding: 8px 12px; border-radius: 4px; max-height: 560px; overflow: auto; }
.hit { padding: 6px 0; border-bottom: 1px dashed #e5e8ef; cursor: pointer; }
.snip { font-size: 12px; color: #7a8599; }
.diff { font-family: monospace; font-size: 12px; white-space: pre-wrap; margin-top: 10px; max-height: 360px; overflow: auto; border: 1px solid #eee; padding: 6px; }
.diff .ADD { background: #e8f7ec; color: #1e7b3c; }
.diff .DEL { background: #fdecec; color: #b3261e; text-decoration: line-through; }
@media (max-width: 900px) { .kb { flex-direction: column; } .left { width: auto; border: none; } .editor { grid-template-columns: 1fr; } }
</style>
