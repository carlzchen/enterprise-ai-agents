<template>
  <div v-if="gates">
    <el-steps :active="activeStep" finish-status="success" align-center class="steps">
      <el-step v-for="(label, key) in STAGES" :key="key" :title="label" />
    </el-steps>
    <div class="actions" v-if="hasRole(['ADMIN','MANAGEMENT']) && project && ['ACTIVE','ON_HOLD'].includes(project.status)">
      <el-button type="success" @click="gate('GO')">{{ project.status === 'ON_HOLD' ? '恢复项目' : project.stage === 'MP' ? '结项' : '阶段门通过（GO）' }}</el-button>
      <el-button v-if="project.status === 'ACTIVE'" type="warning" plain @click="gate('HOLD')">暂停（HOLD）</el-button>
      <el-button type="danger" plain @click="gate('KILL')">终止（KILL）</el-button>
    </div>
    <h4>阶段门评审记录</h4>
    <el-timeline>
      <el-timeline-item v-for="g in gates" :key="g.id" :timestamp="g.gateDate" :type="g.decision === 'GO' ? 'success' : g.decision === 'HOLD' ? 'warning' : 'danger'">
        <b>{{ STAGES[g.fromStage] }}</b> 阶段评审：{{ { GO: '通过', HOLD: '暂停', KILL: '终止' }[g.decision] }}
        <span v-if="g.toStage && g.toStage !== g.fromStage"> → 进入{{ STAGES[g.toStage] }}</span>
        <div class="muted">{{ g.comment }}（{{ g.reviewer }}）</div>
      </el-timeline-item>
    </el-timeline>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { STAGES } from '../api/format'

const props = defineProps({ id: Number })
const emit = defineEmits(['changed'])
const gates = ref(null)
const project = ref(null)
const activeStep = computed(() => {
  if (!project.value) return 0
  const i = Object.keys(STAGES).indexOf(project.value.stage)
  return project.value.status === 'CLOSED' ? 5 : i
})

async function load() {
  gates.value = await http.get(`/plm/projects/${props.id}/gates`)
  const res = await http.get('/views/projects', { params: { size: 500 } })
  project.value = res.records.find(p => p.id === props.id)
}

async function gate(decision) {
  const { value } = await ElMessageBox.prompt('评审意见' + (decision === 'GO' ? '（可选）' : '（必填）'), '阶段门评审', {
    inputValidator: v => decision === 'GO' || (v && v.trim().length > 0) || '请填写评审意见'
  })
  await http.post(`/plm/projects/${props.id}/gate`, { decision, comment: value })
  ElMessage.success('评审已记录')
  await load()
  emit('changed')
}

onMounted(load)
</script>

<style scoped>
.steps { margin: 8px 0 16px; }
.actions { margin: 12px 0; display: flex; gap: 8px; }
h4 { margin: 16px 0 8px; }
</style>
