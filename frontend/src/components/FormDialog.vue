<template>
  <el-dialog v-model="visible" :title="state.title" :width="state.width || '560px'" destroy-on-close>
    <el-form label-width="100px" :model="model">
      <template v-for="f in state.fields" :key="f.prop">
        <el-form-item v-if="f.type !== 'lines'" :label="f.label" :required="f.required">
          <el-select v-if="f.type === 'select'" v-model="model[f.prop]" filterable style="width: 100%" @change="f.onChange && f.onChange(model)">
            <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-date-picker v-else-if="f.type === 'date'" v-model="model[f.prop]" value-format="YYYY-MM-DD" style="width: 100%" />
          <el-input-number v-else-if="f.type === 'number'" v-model="model[f.prop]" :min="f.min ?? 0" :precision="f.precision ?? 2"
                           :step="f.step || 1" controls-position="right" style="width: 100%" />
          <el-input v-else-if="f.type === 'textarea'" v-model="model[f.prop]" type="textarea" :rows="3" />
          <el-switch v-else-if="f.type === 'switch'" v-model="model[f.prop]" />
          <input v-else-if="f.type === 'file'" type="file" :accept="f.accept" @change="e => (model[f.prop] = e.target.files[0] || null)" />
          <el-input v-else v-model="model[f.prop]" />
        </el-form-item>
        <div v-else class="lines">
          <div class="lines-head">
            <b>{{ f.label }}</b>
            <el-button v-if="!f.fixed" size="small" @click="model[f.prop].push({ ...(f.newLine || {}) })">添加行</el-button>
          </div>
          <el-table :data="model[f.prop]" size="small" border>
            <el-table-column v-for="c in f.columns" :key="c.prop" :label="c.label" :min-width="c.width || 120">
              <template #default="{ row }">
                <span v-if="c.type === 'text'">{{ row[c.prop] }}</span>
                <el-select v-else-if="c.type === 'select'" v-model="row[c.prop]" filterable size="small" style="width: 100%"
                           @change="c.onChange && c.onChange(row, model)">
                  <el-option v-for="o in c.options" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
                <el-input-number v-else v-model="row[c.prop]" :min="0" :precision="c.precision ?? 2" size="small"
                                 controls-position="right" style="width: 100%" />
              </template>
            </el-table-column>
            <el-table-column v-if="!f.fixed" width="60">
              <template #default="{ $index }">
                <el-button link type="danger" size="small" @click="model[f.prop].splice($index, 1)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">确定</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

const visible = ref(false)
const saving = ref(false)
const state = reactive({ title: '', fields: [], width: null, onSubmit: null })
const model = reactive({})

/** open({ title, fields, initial, onSubmit(model) }) */
function open(opts) {
  Object.keys(model).forEach(k => delete model[k])
  Object.assign(state, { width: null, ...opts })
  for (const f of opts.fields) model[f.prop] = f.type === 'lines' ? [] : null
  Object.assign(model, JSON.parse(JSON.stringify(opts.initial || {})))
  for (const f of opts.fields) if (f.type === 'lines') model[f.prop] = model[f.prop] || []
  visible.value = true
}

async function submit() {
  const missing = state.fields.find(f => f.required && (model[f.prop] === null || model[f.prop] === '' ||
    (Array.isArray(model[f.prop]) && model[f.prop].length === 0)))
  if (missing) {
    ElMessage.warning(`请填写：${missing.label}`)
    return
  }
  saving.value = true
  try {
    await state.onSubmit(model)
    visible.value = false
  } catch (e) {
    // 错误已由拦截器提示
  } finally {
    saving.value = false
  }
}

defineExpose({ open })
</script>

<style scoped>
.lines { margin: 8px 0 0; }
.lines-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
</style>
