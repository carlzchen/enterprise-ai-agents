<template>
  <div class="ctable">
    <div v-if="title" class="ctitle">{{ title }}<span class="muted">（{{ result.rows.length }} 行）</span></div>
    <el-table :data="result.rows" size="small" border :max-height="maxHeight" stripe>
      <el-table-column v-for="c in result.columns" :key="c.name" :prop="c.name" :label="c.name" :align="c.type === 'text' ? 'left' : 'right'"
                       :min-width="c.type === 'text' ? 120 : 100" show-overflow-tooltip :sortable="result.rows.length > 3">
        <template #default="{ row }">{{ fmtCell(row[c.name], c.type) }}</template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { fmtCell } from '../api/copilot'

defineProps({ result: { type: Object, required: true }, title: String, maxHeight: { type: Number, default: 320 } })
</script>

<style scoped>
.ctable { margin-top: 10px; }
.ctitle { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.ctitle .muted { font-weight: 400; margin-left: 4px; }
</style>
