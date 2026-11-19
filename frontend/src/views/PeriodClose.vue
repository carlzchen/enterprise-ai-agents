<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <span class="muted">月结：试算平衡校验 → 结转制造费用差异 → 结转损益至本年利润（12 月转入未分配利润）→ 锁定期间。必须按顺序结账，已结期间禁止记账。</span>
      </div>
      <el-table :data="periods" size="small" border>
        <el-table-column prop="period" label="会计期间" width="110" />
        <el-table-column label="状态" width="90"><template #default="{ row }">
          <el-tag size="small" :type="row.status === 'CLOSED' ? 'success' : 'warning'">{{ row.status === 'CLOSED' ? '已结账' : '未结账' }}</el-tag>
        </template></el-table-column>
        <el-table-column prop="voucher_count" label="凭证数" align="right" width="90" />
        <el-table-column label="本期利润" align="right"><template #default="{ row }">{{ row.profit == null ? '' : money(row.profit) }}</template></el-table-column>
        <el-table-column prop="closed_by" label="结账人" width="90" />
        <el-table-column label="结账时间" width="170"><template #default="{ row }">{{ row.closed_at ? String(row.closed_at).replace('T', ' ').slice(0, 19) : '' }}</template></el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button v-if="row.status !== 'CLOSED' && hasRole(['ADMIN','FINANCE'])" link type="primary" size="small" @click="close(row)">结账</el-button>
            <el-button v-if="row.status === 'CLOSED' && hasRole(['ADMIN']) && row.period === lastClosed" link type="danger" size="small" @click="reopen(row)">反结账</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http'
import { hasRole } from '../api/auth'
import { money } from '../api/format'

const periods = ref([])
const lastClosed = computed(() => periods.value.find(p => p.status === 'CLOSED')?.period)
const load = async () => { periods.value = await http.get('/gl/periods') }

async function close(row) {
  await ElMessageBox.confirm(`结账 ${row.period}？结账后该期间不能再记账。`, '月结', { type: 'warning' })
  const r = await http.post(`/gl/periods/${row.period}/close`)
  ElMessage.success(`${r.period} 已结账，本期利润 ${money(r.profit)}`)
  load()
}

async function reopen(row) {
  await ElMessageBox.confirm(`反结账 ${row.period}？将删除该期结转凭证并解锁。`, '反结账', { type: 'warning' })
  await http.post(`/gl/periods/${row.period}/reopen`)
  ElMessage.success('已反结账')
  load()
}

onMounted(load)
</script>
