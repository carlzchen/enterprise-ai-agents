<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-button v-if="hasRole(['ADMIN','HR','MANAGEMENT'])" type="primary" @click="publish">发布公告</el-button>
      </div>
      <el-collapse>
        <el-collapse-item v-for="n in list" :key="n.id" :name="n.id">
          <template #title>
            <el-tag size="small" :type="n.category === '制度' ? 'warning' : n.category === '公告' ? '' : 'info'">{{ n.category }}</el-tag>
            <b style="margin: 0 10px">{{ n.title }}</b><span class="muted">{{ n.publish_date }} · {{ n.publisher }}{{ n.dept_name ? ' · ' + n.dept_name : '' }}</span>
          </template>
          <div style="white-space: pre-wrap; line-height: 1.8">{{ n.content }}</div>
        </el-collapse-item>
      </el-collapse>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { openForm } from '../api/shared'

const list = ref([])
async function load() { list.value = await http.get('/oa/notices', { params: { limit: 100 } }) }
function publish() {
  openForm({
    title: '发布公告',
    fields: [
      { prop: 'category', label: '类别', type: 'select', required: true, options: ['公告', '制度', '通知'].map(v => ({ value: v, label: v })) },
      { prop: 'title', label: '标题', required: true }, { prop: 'content', label: '内容', required: true }],
    initial: { category: '通知' },
    onSubmit: async m => { await http.post('/oa/notices', m); ElMessage.success('已发布'); load() }
  })
}
onMounted(load)
</script>
