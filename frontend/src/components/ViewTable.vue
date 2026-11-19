<template>
  <div>
    <div class="toolbar">
      <el-input v-model="kw" placeholder="关键字" clearable style="width: 200px" @keyup.enter="search" @clear="search" />
      <el-select v-for="f in config.filters || []" :key="f.prop" v-model="filters[f.prop]" :placeholder="f.label"
                 clearable style="width: 140px" @change="search">
        <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>
      <el-date-picker v-if="config.dateFilter" v-model="range" type="daterange" value-format="YYYY-MM-DD"
                      start-placeholder="开始日期" end-placeholder="结束日期" style="width: 260px" @change="search" />
      <el-button type="primary" plain @click="search">查询</el-button>
      <span class="spacer" />
      <template v-for="a in config.toolbar || []" :key="a.label">
        <el-button v-if="hasRole(a.roles)" type="primary" @click="a.run(ctx)">{{ a.label }}</el-button>
      </template>
    </div>

    <el-table :data="rows" v-loading="loading" border stripe size="small" height="calc(100vh - 260px)"
              @row-click="row => $emit('row-click', row)" :row-style="config.clickable ? { cursor: 'pointer' } : {}">
      <el-table-column v-for="c in visibleColumns" :key="c.prop" :prop="c.prop" :label="c.label"
                       :width="c.width" :min-width="c.minWidth || 90" :align="alignOf(c)" show-overflow-tooltip>
        <template #default="{ row }">
          <el-tag v-if="c.type === 'status'" :type="(c.map || STATUS)[row[c.prop]]?.[1]" size="small" disable-transitions>
            {{ (c.map || STATUS)[row[c.prop]]?.[0] || row[c.prop] }}
          </el-tag>
          <span v-else class="num">{{ render(c, row) }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="rowActions.length" label="操作" fixed="right" :width="config.actionWidth || 150">
        <template #default="{ row }">
          <template v-for="a in rowActions" :key="a.label">
            <el-button v-if="!a.visible || a.visible(row)" link type="primary" size="small"
                       @click.stop="runAction(a, row)">{{ a.label }}</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination class="pager" background layout="total, sizes, prev, pager, next" :total="total"
                   v-model:current-page="page" v-model:page-size="size" :page-sizes="[20, 50, 100]"
                   @current-change="load" @size-change="search" />
  </div>
</template>

<script setup>
import { computed, reactive, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { STATUS, money, qty, pct, SERIES, DEFECTS, TXN_TYPES, ECN_REASONS, STAGES, DOC_TYPES } from '../api/format'

const props = defineProps({ config: { type: Object, required: true }, fixedFilters: { type: Object, default: () => ({}) } })
defineEmits(['row-click'])

const rows = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const kw = ref('')
const range = ref(null)
const loading = ref(false)
const filters = reactive({})

const DICTS = { series: SERIES, defect: DEFECTS, txn: TXN_TYPES, reason: ECN_REASONS, stage: STAGES, docType: DOC_TYPES }

// 敏感列（如成本）由后端按角色裁剪，返回数据里没有的列不显示
const visibleColumns = computed(() =>
  props.config.columns.filter(c => !c.sensitive || rows.value.length === 0 || c.prop in rows.value[0])
)
const rowActions = computed(() => (props.config.actions || []).filter(a => hasRole(a.roles)))

function alignOf(c) {
  return ['money', 'qty', 'pct', 'int'].includes(c.type) ? 'right' : 'left'
}

function render(c, row) {
  const v = row[c.prop]
  switch (c.type) {
    case 'money': return money(v)
    case 'qty': return qty(v)
    case 'pct': return pct(v)
    case 'bool': return v === true || v === 1 ? '是' : '否'
    case 'dict': return DICTS[c.dict]?.[v] || v
    default: return v
  }
}

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, size: size.value, kw: kw.value, ...props.fixedFilters }
    for (const [k, v] of Object.entries(filters)) if (v !== '' && v != null) params[k] = v
    if (range.value) [params.dateFrom, params.dateTo] = range.value
    const res = await http.get(`/views/${props.config.key}`, { params })
    rows.value = res.records
    total.value = res.total
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

const ctx = { reload: load, http, ElMessage, ElMessageBox }

async function runAction(a, row) {
  try {
    if (a.confirm) await ElMessageBox.confirm(a.confirm(row), '确认', { type: 'warning' })
    const done = await a.run(row, ctx)
    if (done !== false) {
      ElMessage.success(a.successMsg || '操作成功')
      load()
    }
  } catch (e) {
    // 取消或接口错误（接口错误已由拦截器提示）
  }
}

onMounted(load)
defineExpose({ reload: load })
</script>

<style scoped>
.pager { margin-top: 12px; justify-content: flex-end; }
</style>
