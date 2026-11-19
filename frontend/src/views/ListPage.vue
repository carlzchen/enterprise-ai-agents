<template>
  <div class="page">
    <div class="page-card">
      <ViewTable ref="table" :config="config" @row-click="openDetail" />
    </div>
    <FormDialog ref="form" />
    <el-drawer v-if="config.detail" v-model="drawer" :title="config.detailTitle || '详情'" size="70%" destroy-on-close>
      <component :is="config.detail" v-if="drawer" :id="currentId" @changed="table.reload()" />
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import ViewTable from '../components/ViewTable.vue'
import FormDialog from '../components/FormDialog.vue'
import { formDialog } from '../api/shared'

const props = defineProps({ config: { type: Object, required: true } })
const table = ref()
const form = ref()
const drawer = ref(false)
const currentId = ref(null)

function openDetail(row) {
  if (!props.config.detail) return
  currentId.value = row.id
  drawer.value = true
}

onMounted(() => {
  formDialog.value = form.value
})
</script>
