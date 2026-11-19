import { shallowRef } from 'vue'
import http from './http'

/** 全局表单弹窗，由 ListPage 挂载 */
export const formDialog = shallowRef(null)
export const openForm = opts => formDialog.value.open(opts)

let optionsCache = null
export async function options(refresh = false) {
  if (!optionsCache || refresh) optionsCache = await http.get('/md/options')
  return optionsCache
}

export const opt = (list, labelFn = x => `${x.code} ${x.name}`) => list.map(x => ({ value: x.id, label: labelFn(x) }))
export const enumOpts = dict => Object.entries(dict).map(([value, label]) => ({ value, label }))
