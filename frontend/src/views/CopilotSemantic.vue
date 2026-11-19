<template>
  <div class="page">
    <div class="page-card">
      <el-tabs v-model="tab">
        <el-tab-pane label="指标口径" name="metrics">
          <div class="toolbar">
            <el-input v-model="kw" placeholder="搜索指标名称或别名" clearable style="width: 240px" />
            <span class="muted">每个指标只有一个定义，看板、问数、导出都用它。原子指标直接从数据里数出来，派生指标由原子指标计算。</span>
          </div>
          <el-table :data="shownMetrics" size="small" border>
            <el-table-column prop="name" label="指标" width="140" fixed />
            <el-table-column label="类型" width="80">
              <template #default="{ row }"><el-tag size="small" :type="row.type === 'atomic' ? 'info' : ''">{{ row.type === 'atomic' ? '原子' : '派生' }}</el-tag></template>
            </el-table-column>
            <el-table-column label="公式" min-width="320">
              <template #default="{ row }">{{ row.formula }}<div v-if="row.note" class="muted small">{{ row.note }}</div></template>
            </el-table-column>
            <el-table-column label="别名" min-width="160"><template #default="{ row }">{{ row.aliases.join('、') }}</template></el-table-column>
            <el-table-column label="可用维度" min-width="200"><template #default="{ row }">{{ row.dims.join('、') }}</template></el-table-column>
            <el-table-column prop="unit" label="单位" width="70" />
            <el-table-column label="权限" width="110">
              <template #default="{ row }">
                <el-tag v-if="row.sensitivity !== 'normal'" size="small" type="warning">{{ row.sensitivity === 'salary' ? '薪酬' : '成本毛利' }}</el-tag>
                <span v-else class="muted">普通</span>
              </template>
            </el-table-column>
            <el-table-column label="模板" width="110"><template #default="{ row }">{{ row.templates.join(' ') }}</template></el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="分析模板" name="templates">
          <div class="toolbar"><span class="muted">高频问题用人工写好并验证过的查询，模型只负责选模板和填参数。点示例问题可以直接去问。</span></div>
          <el-table :data="templates" size="small" border>
            <el-table-column prop="id" label="编号" width="64" />
            <el-table-column prop="name" label="模板" width="170" />
            <el-table-column prop="intent" label="意图" width="100" />
            <el-table-column prop="desc" label="说明" min-width="260" />
            <el-table-column label="可分组" min-width="170"><template #default="{ row }">{{ row.dims.join('、') || '—' }}</template></el-table-column>
            <el-table-column label="可过滤" min-width="150"><template #default="{ row }">{{ row.filters.join('、') || '—' }}</template></el-table-column>
            <el-table-column label="示例问题" min-width="260">
              <template #default="{ row }">
                <el-tag v-for="q in row.examples.slice(0, 3)" :key="q" size="small" effect="plain" class="ex" :class="{ off: !row.usable }"
                        @click="row.usable && $router.push({ path: '/copilot', query: { q } })">{{ q }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="我能用" width="70" align="center">
              <template #default="{ row }"><el-tag size="small" :type="row.usable ? 'success' : 'info'">{{ row.usable ? '是' : '否' }}</el-tag></template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="我能查的数据" name="views">
          <div class="toolbar"><span class="muted">这里列的是你的角色能查询的数据。"范围"决定你能看到哪些行，隐藏字段不会出现在任何结果里。</span></div>
          <el-collapse>
            <el-collapse-item v-for="v in views" :key="v.name" :name="v.name">
              <template #title>
                <b>{{ v.title }}</b><span class="muted vname">{{ v.name }}</span>
                <el-tag size="small" :type="{ all: 'success', dept: 'warning', self: 'info' }[v.access]">{{ { all: '全部', dept: '本部门', self: '仅本人' }[v.access] }}</el-tag>
                <span v-if="v.data_until" class="muted vname">数据截至 {{ v.data_until }}</span>
              </template>
              <p>{{ v.desc }}</p>
              <p v-if="v.hidden.length" class="hidden">对你隐藏的字段：{{ v.hidden.map(x => x.split('，')[0]).join('、') }}</p>
              <el-table :data="v.columns.filter(c => c.desc)" size="small" border>
                <el-table-column prop="name" label="字段" width="180" /><el-table-column prop="desc" label="含义" />
              </el-table>
            </el-collapse-item>
          </el-collapse>
        </el-tab-pane>

        <el-tab-pane label="制度文档" name="policy">
          <div class="toolbar">
            <el-upload v-if="canManage" :show-file-list="false" :http-request="upload" accept=".md,.txt,.docx">
              <el-button type="primary">上传或更新制度</el-button>
            </el-upload>
            <span class="muted">支持 Markdown、纯文本、Word。同名文件再次上传会替换旧版本，旧内容不会再被检索到。</span>
          </div>
          <el-table :data="docs" size="small" border>
            <el-table-column prop="title" label="制度名称" min-width="220" />
            <el-table-column prop="version" label="版本" width="80" />
            <el-table-column prop="chunk_count" label="切片数" width="80" align="right" />
            <el-table-column prop="status" label="状态" width="90" />
            <el-table-column prop="uploaded_by" label="上传人" width="100" />
            <el-table-column label="更新时间" width="170"><template #default="{ row }">{{ String(row.updated_at).replace('T', ' ').slice(0, 19) }}</template></el-table-column>
            <el-table-column v-if="canManage" label="操作" width="80">
              <template #default="{ row }"><el-button link type="danger" @click="remove(row)">删除</el-button></template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { hasRole } from '../api/auth'
import copilot from '../api/copilot'

const tab = ref('metrics')
const kw = ref('')
const metrics = ref([]), templates = ref([]), views = ref([]), docs = ref([])
const canManage = hasRole(['ADMIN', 'HR'])
const shownMetrics = computed(() => metrics.value.filter(m => !kw.value || m.name.includes(kw.value) || m.aliases.some(a => a.includes(kw.value))))

async function load() {
  [metrics.value, templates.value, views.value, docs.value] = await Promise.all([
    copilot.get('/semantic/metrics'), copilot.get('/semantic/templates'), copilot.get('/semantic/views'), copilot.get('/policy/documents')])
}

async function upload({ file }) {
  const form = new FormData()
  form.append('file', file)
  const rec = await copilot.post('/policy/upload', form)
  ElMessage.success(`已入库：${rec.title}（${rec.chunk_count} 个切片）`)
  docs.value = await copilot.get('/policy/documents')
}

async function remove(row) {
  await ElMessageBox.confirm(`删除后「${row.title}」不再能被问到，确定吗？`, '删除制度', { type: 'warning' })
  await copilot.delete(`/policy/documents/${row.doc_key}`)
  docs.value = await copilot.get('/policy/documents')
}

onMounted(load)
</script>

<style scoped>
.small { font-size: 12px; }
.ex { margin: 2px; cursor: pointer; }
.ex.off { cursor: not-allowed; opacity: .5; }
.vname { margin: 0 10px; font-size: 12px; }
.hidden { color: #c45656; font-size: 13px; }
</style>
