<template>
  <div v-if="d">
    <el-descriptions :column="3" border size="small">
      <el-descriptions-item label="8D 编号">{{ d.report_no }}</el-descriptions-item>
      <el-descriptions-item label="客诉">{{ d.complaint_no }}（{{ d.customer_name }}）</el-descriptions-item>
      <el-descriptions-item label="状态"><el-tag size="small" :type="d.status === 'CLOSED' ? 'success' : 'warning'">{{ d.status === 'CLOSED' ? '已结案' : '进行中' }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="产品">{{ d.product_code }}</el-descriptions-item>
      <el-descriptions-item label="严重度">{{ d.severity }}</el-descriptions-item>
      <el-descriptions-item label="开立 / 期限 / 结案">{{ d.opened_date }} / {{ d.due_date }} / {{ d.closed_date || '-' }}（{{ d.age_days }} 天）</el-descriptions-item>
    </el-descriptions>
    <el-steps :active="d.status === 'CLOSED' ? 8 : d.current_step - 1" finish-status="success" style="margin: 16px 0" simple>
      <el-step v-for="s in STEPS" :key="s[0]" :title="s[1]" />
    </el-steps>
    <div v-for="(s, i) in STEPS" :key="s[0]" class="step">
      <div class="label">{{ s[1] }}</div>
      <div class="content">{{ d[s[0]] || '—' }}</div>
      <el-button v-if="d.status !== 'CLOSED' && hasRole(['ADMIN','QUALITY'])" link type="primary" size="small" @click="edit(i + 1, s)">填写</el-button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const d = ref(null)
const STEPS = [['d1_team', 'D1 团队'], ['d2_problem', 'D2 问题描述'], ['d3_containment', 'D3 临时措施'], ['d4_root_cause', 'D4 根本原因'],
  ['d5_actions', 'D5 永久措施'], ['d6_verification', 'D6 效果验证'], ['d7_prevention', 'D7 预防再发'], ['d8_closure', 'D8 结案']]
async function load() { d.value = await http.get(`/qms/8d/${props.id}`) }
async function edit(step, s) {
  const { value } = await ElMessageBox.prompt(s[1], `更新 ${d.value.report_no}`, { inputValue: d.value[s[0]] || '', inputType: 'textarea' })
  await http.post(`/qms/8d/${props.id}/step`, { step, content: value })
  ElMessage.success(step === 8 ? '8D 已结案' : '已保存')
  await load(); emit('changed')
}
onMounted(load)
</script>

<style scoped>
.step { display: flex; gap: 12px; padding: 8px 0; border-bottom: 1px dashed #e5e8ef; align-items: flex-start; }
.label { width: 110px; font-weight: 600; color: #1f3864; flex-shrink: 0; }
.content { flex: 1; white-space: pre-wrap; line-height: 1.7; }
</style>
