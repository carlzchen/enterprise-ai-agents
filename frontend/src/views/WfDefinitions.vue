<template>
  <div class="page">
    <div class="page-card">
      <p class="muted">审批人类型：MANAGER 直属上级（level 级）｜DEPT_HEAD 申请人部门负责人｜DEPT_OF 指定部门负责人｜ROLE 角色全部人员｜USER 指定账号。
        mode：OR 或签 / AND 会签。condition：如 <code>amount >= 5000</code>、<code>days >= 3 || leaveType == 'SICK'</code>。
        申请人本人自动跳过，前序节点已审批的人自动去重。保存即生成新版本，运行中的流程继续按原版本执行。</p>
      <el-table :data="defs" size="small" border>
        <el-table-column prop="code" label="编码" width="100" />
        <el-table-column prop="name" label="名称" width="120" />
        <el-table-column prop="version" label="版本" width="60" />
        <el-table-column label="审批节点" min-width="420"><template #default="{ row }">
          <div v-for="(n, i) in nodes(row)" :key="i">{{ i + 1 }}. <b>{{ n.name }}</b>
            <span class="muted">　{{ n.type }}{{ n.level ? ' L' + n.level : '' }}{{ n.dept ? ' ' + n.dept : '' }}{{ n.role ? ' ' + n.role : '' }}{{ n.user ? ' ' + n.user : '' }}
              {{ n.mode === 'AND' ? '会签' : '' }}{{ n.condition ? '　当 ' + n.condition : '' }}</span></div>
        </template></el-table-column>
        <el-table-column v-if="hasRole(['ADMIN','HR'])" label="操作" width="80"><template #default="{ row }">
          <el-button link type="primary" size="small" @click="edit(row)">编辑</el-button>
        </template></el-table-column>
      </el-table>
    </div>
    <el-dialog v-model="editing" :title="`编辑流程：${current?.name}（保存为 v${(current?.version || 0) + 1}）`" width="720px">
      <el-input v-model="json" type="textarea" :rows="16" style="font-family: monospace" />
      <template #footer><el-button type="primary" @click="save">保存新版本</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'

const defs = ref([])
const editing = ref(false)
const current = ref(null)
const json = ref('')
const nodes = r => { try { return JSON.parse(r.definition).nodes } catch { return [] } }
async function load() { defs.value = await http.get('/oa/definitions') }
function edit(r) { current.value = r; json.value = JSON.stringify(JSON.parse(r.definition), null, 2); editing.value = true }
async function save() {
  try { JSON.parse(json.value) } catch { return ElMessage.error('JSON 格式错误') }
  const r = await http.post('/oa/definitions', { code: current.value.code, name: current.value.name, definition: json.value, formSchema: current.value.form_schema })
  ElMessage.success(`已保存为 v${r.version}`)
  editing.value = false
  load()
}
onMounted(load)
</script>
