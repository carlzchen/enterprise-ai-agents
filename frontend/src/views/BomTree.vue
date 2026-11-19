<template>
  <div class="page">
    <div class="bar">
      <el-select v-model="productId" filterable placeholder="选择产品" style="width: 320px" @change="load">
        <el-option v-for="p in products" :key="p.id" :label="`${p.code} ${p.name}`" :value="p.id" />
      </el-select>
      <span class="lbl">数量</span>
      <el-input-number v-model="qtyIn" :min="1" :precision="0" controls-position="right" @change="load" />
      <el-button @click="expandAll = !expandAll; rerender()">{{ expandAll ? '全部折叠' : '全部展开' }}</el-button>
    </div>
    <template v-if="data">
      <div class="summary">
        <span><b>{{ data.productCode }}</b> {{ data.productName }}</span>
        <el-tag size="small">{{ data.levels }} 层</el-tag>
        <span>× {{ data.qty }}，外购件材料成本（当月参考价）<b>{{ money(data.materialCost) }}</b></span>
      </div>
      <el-table :key="tableKey" :data="data.tree" row-key="key" border size="small" :default-expand-all="expandAll"
                :tree-props="{ children: 'children' }">
        <el-table-column label="物料" min-width="260">
          <template #default="{ row }">
            <b v-if="row.supplyType === 'MAKE'">{{ row.code }}</b><span v-else>{{ row.code }}</span>
            <span class="muted"> {{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="level" label="层级" width="60" align="center" />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.supplyType === 'MAKE' ? 'warning' : 'info'">{{ row.supplyType === 'MAKE' ? `自制 ${row.lineCode}` : '外购' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="单位用量" align="right" width="100"><template #default="{ row }">{{ qty(row.qtyPer) }}</template></el-table-column>
        <el-table-column label="总需求" align="right" width="110"><template #default="{ row }">{{ qty(row.totalQty) }} {{ row.uom }}</template></el-table-column>
        <el-table-column label="现存量" align="right" width="110"><template #default="{ row }">{{ qty(row.stock) }}</template></el-table-column>
        <el-table-column label="单位成本" align="right" width="100"><template #default="{ row }">{{ money(row.unitCost) }}</template></el-table-column>
        <el-table-column label="金额" align="right" width="110"><template #default="{ row }">{{ money(row.extCost) }}</template></el-table-column>
        <el-table-column label="替代料" width="80" align="center">
          <template #default="{ row }"><el-tag v-if="row.substitutes" size="small" type="success">{{ row.substitutes }}</el-tag></template>
        </el-table-column>
      </el-table>
      <p class="muted">自制件成本 = 下层物料参考价 + 本层加工费，逐层卷积；金额只在最底层外购件上汇总，避免重复计算。</p>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import http from '../api/http'
import { options } from '../api/shared'
import { money, qty } from '../api/format'

const products = ref([])
const productId = ref(null)
const qtyIn = ref(1)
const data = ref(null)
const expandAll = ref(true)
const tableKey = ref(0)

function rerender() { tableKey.value++ }

async function load() {
  if (!productId.value) return
  data.value = await http.get(`/md/products/${productId.value}/bom-tree`, { params: { qty: qtyIn.value } })
  rerender()
}

onMounted(async () => {
  const o = await options()
  products.value = o.products
  productId.value = (o.products.find(p => p.code.startsWith('KL-SV')) || o.products[0])?.id
  await load()
})
</script>

<style scoped>
.page { padding: 4px; }
.bar { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.lbl { color: var(--el-text-color-secondary); }
.summary { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
</style>
