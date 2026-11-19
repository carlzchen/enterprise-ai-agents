<template>
  <div class="page">
    <div class="page-card">
      <div class="toolbar">
        <el-radio-group v-model="status" size="small" @change="load">
          <el-radio-button value="OPEN">待处理（{{ status === 'OPEN' ? tasks.length : '…' }}）</el-radio-button>
          <el-radio-button value="DONE">已处理</el-radio-button>
        </el-radio-group>
        <span class="muted">AI 负责读懂和起草，数字由规则计算，发给客户的内容都要你确认。</span>
        <span class="spacer" />
        <el-button size="small" @click="load">刷新</el-button>
      </div>
      <el-table :data="tasks" size="small" border stripe height="calc(100vh - 230px)" @row-click="open">
        <el-table-column label="类型" width="120"><template #default="{ row }"><el-tag size="small" :type="TASK_TAG[row.type]">{{ row.type_cn }}</el-tag></template></el-table-column>
        <el-table-column prop="title" label="事项" min-width="360" show-overflow-tooltip />
        <el-table-column prop="customer_name" label="客户" min-width="180" show-overflow-tooltip />
        <el-table-column label="处理角色" width="90"><template #default="{ row }">{{ ROLE_CN[row.role] || row.role }}</template></el-table-column>
        <el-table-column v-if="status === 'OPEN'" label="最晚处理时间" width="150"><template #default="{ row }">
          <span :class="{ bad: overdue(row) }">{{ fmt(row.due_at) }}<template v-if="overdue(row)">　已超时</template></span>
        </template></el-table-column>
        <template v-else>
          <el-table-column prop="done_by" label="处理人" width="90" />
          <el-table-column prop="result" label="结果" min-width="160" show-overflow-tooltip />
          <el-table-column label="处理时间" width="140"><template #default="{ row }">{{ fmt(row.done_at) }}</template></el-table-column>
        </template>
      </el-table>
    </div>

    <el-drawer v-model="show" :title="d?.task ? `${d.task.type_cn}｜${d.task.title}` : ''" size="780px" destroy-on-close>
      <template v-if="d">
        <div v-if="d.opportunity" class="bar">
          <el-tag size="small" effect="plain">{{ d.opportunity.customer_name }}</el-tag>
          <el-tag size="small" type="info" effect="plain">商机 {{ d.opportunity.opp_id }} · {{ d.opportunity.status_cn }}</el-tag>
          <el-tag v-if="!d.task.mine" size="small" type="warning">这个待办由{{ ROLE_CN[d.task.role] }}处理，你只能查看</el-tag>
        </div>

        <!-- 来信 -->
        <div v-if="d.mail" class="box">
          <div class="box-title">来信　<span class="muted">{{ d.mail.from_name }} &lt;{{ d.mail.real_from || d.mail.from_addr }}&gt;　{{ fmt(d.mail.received_at) }}</span></div>
          <div class="subject">{{ d.mail.subject }}</div>
          <pre class="mail">{{ d.mail.body_new }}</pre>
          <div v-for="a in d.mail.attachments" :key="a.name" class="att">
            <el-tag size="small" type="info">附件 {{ a.name }}</el-tag><span v-if="!a.parsed" class="bad">没能解析，请打开原件核对</span>
            <pre v-if="a.preview" class="mail small">{{ a.preview }}</pre>
          </div>
          <div class="muted">系统的理解：{{ d.mail.summary || '（无）' }}</div>
        </div>

        <!-- 邮件分类待确认 -->
        <template v-if="d.task.type === 'TRIAGE'">
          <div class="note" v-for="r in d.task.payload.reasons || [d.mail?.pending_reason]" :key="r">需要你确认的原因：{{ r }}</div>
          <el-form label-width="90px">
            <el-form-item label="这是什么事">
              <el-select v-model="f.intents" multiple style="width: 100%"><el-option v-for="(v, k) in meta.intents" :key="k" :value="k" :label="v" /></el-select>
            </el-form-item>
            <el-form-item label="哪个客户">
              <el-select v-model="f.customer_id" filterable clearable style="width: 100%" :placeholder="d.mail?.party_name || '请选择'">
                <el-option v-for="c in customers" :key="c.id" :value="c.id" :label="`${c.code} ${c.name}`" />
              </el-select>
            </el-form-item>
          </el-form>
          <div class="actions">
            <el-button type="primary" :disabled="!d.task.mine" @click="act(() => api.post('/mails/correct', { id: d.mail.message_id, intents: f.intents, customer_id: f.customer_id || null }), '已确认并分派')">确认并分派</el-button>
            <el-button :disabled="!d.task.mine" @click="act(() => api.post('/mails/correct', { id: d.mail.message_id, archive: true }), '已归档')">和业务无关，归档</el-button>
          </div>
        </template>

        <!-- 发件闸门 -->
        <template v-if="d.outbound">
          <div class="box">
            <div class="box-title">{{ OB_CN[d.outbound.kind] }}　<span class="muted">发给 {{ d.outbound.to_addr }}</span>
              <el-tag size="small" :type="d.outbound.drafted_by === 'llm' ? '' : 'info'" effect="plain" style="margin-left: 8px">{{ d.outbound.drafted_by === 'llm' ? '模型起草，数字已核对' : '按固定模板生成' }}</el-tag>
            </div>
            <div class="subject">{{ d.outbound.subject }}</div>
            <el-input v-model="f.text" type="textarea" :rows="12" :disabled="d.outbound.status !== 'PENDING' || !d.task.mine" />
            <div class="muted" style="margin-top: 4px">可以直接改。确认之前不会发出去；发出后原稿和你改过的版本都会留档。</div>
          </div>
          <div v-if="d.outbound.status === 'PENDING'" class="actions">
            <el-button type="primary" :disabled="!d.task.mine" @click="act(() => api.post(`/outbound/${d.outbound.action_id}/confirm`, { text: f.text }), '已发出')">确认发送</el-button>
            <el-button :disabled="!d.task.mine" @click="act(() => api.post(`/outbound/${d.outbound.action_id}/discard`, { result: '自己另写' }), '已弃用')">不用这份，我自己写</el-button>
          </div>
          <div v-else class="muted">已处理：{{ ADOPT_CN[d.outbound.adoption] }}（{{ d.outbound.confirmed_by }}）</div>
        </template>

        <!-- 选型 -->
        <template v-if="d.inquiry && d.task.type === 'SELECTION'">
          <div v-for="(item, i) in d.inquiry.items" :key="i" class="box">
            <div class="box-title">需求 {{ i + 1 }}　<span class="muted">{{ item.raw_text }}</span></div>
            <div class="bar">
              <el-tag size="small" :type="{ EXACT: 'success', NEAR: 'warning', NONE: 'danger' }[d.inquiry.selection[i].grade]">{{ { EXACT: '完全匹配', NEAR: '近似匹配', NONE: '无匹配' }[d.inquiry.selection[i].grade] }}</el-tag>
              <span class="muted">{{ d.inquiry.selection[i].by }}　年用量 {{ item.annual_volume || '未给' }}　目标价 {{ item.target_price || '未给' }}</span>
            </div>
            <div v-for="c in d.inquiry.selection[i].candidates" :key="c.model" class="cand">
              <b>{{ c.model }}</b>　{{ c.name }}　<span class="muted">满足 {{ c.satisfied }} / {{ c.total }} 项</span>
              <el-table :data="c.rows" size="small" border style="margin-top: 4px">
                <el-table-column prop="param" label="参数" width="140" /><el-table-column prop="required" label="客户要求" /><el-table-column prop="actual" label="这个型号" />
                <el-table-column label="是否满足" width="110"><template #default="{ row }"><span :class="row.ok ? 'good' : 'bad'">{{ row.ok ? '满足' : '不满足' }}</span><span class="muted"> {{ row.note }}</span></template></el-table-column>
              </el-table>
            </div>
            <el-form-item label="结论" label-width="50px" style="margin-top: 8px">
              <el-select v-model="f.models[i]" filterable clearable placeholder="没有合适的就留空" style="width: 100%">
                <el-option v-for="p in d.products" :key="p.code" :value="p.code" :label="`${p.code}　${p.name}`" />
              </el-select>
            </el-form-item>
          </div>
          <div class="muted">「是否满足」是程序按数值比出来的，不是模型判断的。</div>
          <div class="actions"><el-button type="primary" :disabled="!d.task.mine" @click="act(() => api.post(`/inquiries/${d.inquiry.inquiry_id}/confirm`, { models: f.models.map(x => x || null) }), '已确认，开始出报价')">确认选型</el-button></div>
        </template>

        <!-- 非标评估（多 Agent） -->
        <template v-if="d.task.type === 'NONSTD_REVIEW' && d.inquiry">
          <div class="note">标准品里没有完全匹配的型号，下面是多 Agent 评估的结果：技术方案由技术 Agent 给出，成本、交期由程序按方案计算，商务建议按定价规则给出，审查按清单核对过各部分是否一致。请逐项确认。</div>
          <div v-for="(item, i) in d.inquiry.items" :key="i" class="box">
            <div class="box-title">需求 {{ i + 1 }}　<span class="muted">{{ item.raw_text }}</span></div>
            <template v-if="evalOf(i)">
              <template v-for="ev in [evalOf(i)]" :key="ev.eval_id">
                <div v-if="ev.status !== 'DONE'" class="bad">评估{{ ev.status === 'FAILED' ? '失败：' + (ev.report?.error || '') : '还在进行中' }}</div>
                <template v-else>
                  <div class="bar">
                    <el-tag size="small" :type="ev.report.tech?.feasibility === '可行' ? 'success' : ev.report.tech?.feasibility === '有条件可行' ? 'warning' : 'danger'">{{ ev.report.tech?.feasibility }}</el-tag>
                    <el-tag size="small" effect="plain">技术风险 {{ ev.report.tech?.risk }}</el-tag>
                    <el-tag size="small" type="info" effect="plain">基础型号 {{ ev.report.tech?.base_model || '无（需要新开发）' }}</el-tag>
                    <span class="muted">审查 {{ ev.report.rounds }} 轮　{{ ev.seconds }} 秒　{{ ev.tokens }} tokens</span>
                  </div>
                  <div class="sub">改型方案</div>
                  <el-table :data="ev.report.tech?.changes || []" size="small" border>
                    <el-table-column label="类型" width="90"><template #default="{ row }">{{ CHANGE_CN[row.type] || row.type }}</template></el-table-column>
                    <el-table-column prop="description" label="改什么" min-width="200" />
                    <el-table-column label="物料" width="170"><template #default="{ row }">{{ row.material_from || '-' }} → {{ row.material_to || (row.new_part ? '新开物料' : '-') }}</template></el-table-column>
                    <el-table-column label="依据" width="90"><template #default="{ row }"><el-tag v-for="e in row.evidence" :key="e" size="small" type="info" effect="plain" class="ev" @click="showEvidence(ev, e)">{{ e }}</el-tag></template></el-table-column>
                  </el-table>
                  <div class="muted" style="margin-top: 4px">{{ ev.report.tech?.reasoning }}</div>
                  <div v-if="ev.report.tech?.open_questions?.length" class="note">需要向客户确认：{{ ev.report.tech.open_questions.join('；') }}</div>
                  <div class="nums">
                    <div v-if="ev.report.cost?.unit_cost != null"><div class="num">{{ money(ev.report.cost.base_unit_cost) }} → {{ money(ev.report.cost.unit_cost) }}</div><div class="muted">单位成本（基础 → 改型）{{ ev.report.cost.is_estimate ? '，含估算' : '' }}</div></div>
                    <div v-if="ev.report.cost?.nre"><div class="num">{{ money(ev.report.cost.nre.nre_total) }}</div><div class="muted">一次性费用（模具 {{ money(ev.report.cost.nre.tooling_total) }}）</div></div>
                    <div v-if="ev.report.lead"><div class="num">{{ ev.report.lead.sample_days ?? '-' }} / {{ ev.report.lead.first_batch_days }} 天</div><div class="muted">打样 / 首批量产</div></div>
                    <div v-if="ev.report.commercial"><div class="num">{{ money(ev.report.commercial.tiers[0].unit_price) }}</div><div class="muted">建议起步价（底价 {{ money(ev.report.commercial.floor_price) }}）</div></div>
                  </div>
                  <div v-if="ev.report.cost?.unit_cost == null && ev.report.cost" class="muted">成本明细只有管理层和财务可见。</div>
                  <div v-if="ev.report.commercial" class="muted">一次性费用：{{ ev.report.commercial.nre_policy }}。{{ ev.report.commercial.recommendation }}</div>
                  <div v-for="r in ev.report.lead?.risks || []" :key="r" class="muted">交期风险：{{ r }}</div>
                  <div v-for="w in ev.report.warnings" :key="w" class="bad">注意：{{ w }}</div>
                  <div v-for="x in ev.report.issues" :key="x.message" class="bad">未解决：{{ x.rule }}　{{ x.message }}</div>
                  <el-collapse class="detail">
                    <el-collapse-item :title="`各 Agent 的工作过程（${ev.trace?.agents?.length || 0} 次运行，${ev.trace?.evidence?.length || 0} 次工具调用）`">
                      <div v-for="(a, k) in ev.trace?.agents || []" :key="k" class="agent">
                        <b>{{ AGENT_CN[a.agent] || a.agent }}</b>　<span class="muted">{{ a.seconds }} 秒　{{ a.ok ? '已交卷' : '没有交卷：' + a.error }}</span>
                        <div v-for="(st, j) in a.steps" :key="j" class="step">· {{ st.tool }}<span v-if="st.evidence_id" class="muted">（{{ st.evidence_id }}）</span><span v-if="st.error" class="bad">　{{ st.error }}</span></div>
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </template>
                <el-switch v-model="f.decisions[i].accept" active-text="按这个方案报价" inactive-text="不报这一项" :disabled="!d.task.mine" style="margin-top: 8px" />
              </template>
            </template>
            <template v-else>
              <div class="muted">标准品完全匹配：{{ d.inquiry.selection[i].candidates[0]?.model }}</div>
              <el-select v-model="f.decisions[i].model" filterable clearable style="width: 100%; margin-top: 6px" :disabled="!d.task.mine">
                <el-option v-for="p in d.products" :key="p.code" :value="p.code" :label="`${p.code}　${p.name}`" />
              </el-select>
            </template>
          </div>
          <div class="actions"><el-button type="primary" :disabled="!d.task.mine" @click="act(() => api.post(`/inquiries/${d.inquiry.inquiry_id}/nonstd`, { decisions: f.decisions.map(x => ({ accept: x.accept || !!x.model, model: x.model || null })) }), '已确认，开始出报价')">确认</el-button></div>
        </template>

        <!-- 报价核对、审批 -->
        <template v-if="d.quote && ['QUOTE_REVIEW', 'APPROVAL'].includes(d.task.type)">
          <QuoteCard :q="d.quote" />
          <el-table :data="f.tiers" size="small" border>
            <el-table-column label="数量阶梯" width="140"><template #default="{ row }">{{ row.qty }} 台及以上</template></el-table-column>
            <el-table-column label="单价（元）" width="170"><template #default="{ row }">
              <el-input-number v-if="d.task.type === 'QUOTE_REVIEW'" v-model="row.unit_price" :precision="2" :step="0.5" :min="0" size="small" controls-position="right" @change="preview" />
              <span v-else>{{ money(row.unit_price) }}</span>
            </template></el-table-column>
            <el-table-column label="毛利率" width="100"><template #default="{ row }"><span :class="{ bad: row.margin < d.quote.min_margin }">{{ pct(row.margin) }}</span></template></el-table-column>
            <el-table-column label="系统算出的价格"><template #default="{ $index }">{{ money(d.quote.system_tiers?.[$index]?.unit_price) }}</template></el-table-column>
          </el-table>
          <template v-if="d.task.type === 'QUOTE_REVIEW'">
            <div class="note">按现在的价格需要<b>{{ f.approver || d.quote.approver || '销售经理' }}</b>审批。<span v-if="f.below" class="bad">毛利率低于最低毛利率，必须写理由。</span></div>
            <el-input v-model="f.reason" placeholder="调价或低毛利的理由（低于最低毛利率时必填）" />
            <div class="actions"><el-button type="primary" :disabled="!d.task.mine" @click="act(() => api.post('/quotes/submit', { ref: d.task.ref_id, tiers: f.tiers, reason: f.reason }), '已提交审批')">提交审批</el-button></div>
          </template>
          <template v-else>
            <div class="note">需要<b>{{ d.quote.approver }}</b>审批。<span v-if="d.quote.reason">销售的理由：{{ d.quote.reason }}</span><span v-if="d.quote.condition">　附带条件：{{ d.quote.condition }}</span></div>
            <el-input v-model="f.note" placeholder="审批意见（驳回时请写原因）" />
            <div class="actions">
              <el-button type="primary" :disabled="!d.task.mine" @click="act(() => api.post('/quotes/approve', { ref: d.task.ref_id, ok: true, note: f.note }), '已通过')">通过</el-button>
              <el-button type="danger" plain :disabled="!d.task.mine" @click="act(() => api.post('/quotes/approve', { ref: d.task.ref_id, ok: false, note: f.note }), '已驳回')">驳回</el-button>
            </div>
          </template>
        </template>

        <!-- 议价 -->
        <template v-if="d.negotiation && d.task.type === 'NEGOTIATION'">
          <div class="nums">
            <div><div class="num">{{ money(d.negotiation.analysis.current_price) }}</div><div class="muted">当前报价</div></div>
            <div><div class="num">{{ d.negotiation.analysis.target_price == null ? '未给出' : money(d.negotiation.analysis.target_price) }}</div><div class="muted">客户目标价</div></div>
            <div><div class="num">{{ money(d.negotiation.analysis.floor_price) }}</div><div class="muted">底价</div></div>
            <div><div class="num">{{ d.negotiation.analysis.gap == null ? '-' : money(d.negotiation.analysis.gap) }}</div><div class="muted">差距</div></div>
            <div><div class="num">{{ money(d.negotiation.analysis.last_deal_price) }}</div><div class="muted">上次成交价</div></div>
          </div>
          <div class="muted">客户的理由：{{ d.negotiation.analysis.customer_reason || '没有说' }}　数量：{{ d.negotiation.analysis.qty }}　（以上数字都是程序算的）</div>
          <el-radio-group v-model="f.strategy" class="options" @change="pick">
            <el-radio v-for="o in d.negotiation.analysis.options" :key="o.strategy" :value="o.strategy" border>
              <b>{{ o.title }}</b><template v-if="o.price">　{{ money(o.price) }} 元，毛利率 {{ pct(o.margin) }}</template>
              <el-tag v-if="o.need_approval" size="small" type="danger" effect="plain" style="margin-left: 6px">要审批</el-tag>
              <div class="muted why">{{ o.why }}</div>
            </el-radio>
          </el-radio-group>
          <el-form label-width="90px" style="margin-top: 10px">
            <el-form-item v-if="needPrice" label="新价格"><el-input-number v-model="f.price" :precision="2" :step="0.5" :min="0" controls-position="right" /></el-form-item>
            <el-form-item v-if="f.strategy === 'TRADE_TERMS'" label="交换条件"><el-input v-model="f.condition" placeholder="例如：起订量提高到 10000 台，账期缩短到 30 天" /></el-form-item>
            <el-form-item label="理由"><el-input v-model="f.reason" placeholder="低于最低毛利率或放弃时请填写" /></el-form-item>
          </el-form>
          <div class="actions"><el-button type="primary" :disabled="!d.task.mine || !f.strategy" @click="act(() => api.post('/negotiations/decide', { ref: d.task.ref_id, strategy: f.strategy, price: needPrice ? f.price : null, condition: f.condition, reason: f.reason }), '已决定')">就这么办</el-button></div>
        </template>

        <!-- 订单差异、建单 -->
        <template v-if="d.order">
          <div class="box">
            <div class="box-title">PO {{ d.order.po_no }}　<span class="muted">金额 {{ money(d.order.data.amount) }}　交期 {{ d.order.data.required_date || '未写' }}　付款 {{ d.order.data.payment_days ?? '未写' }} 天</span></div>
            <el-table :data="d.order.data.lines" size="small" border>
              <el-table-column prop="model" label="型号" /><el-table-column prop="qty" label="数量" width="100" align="right" />
              <el-table-column label="单价" width="110" align="right"><template #default="{ row }">{{ money(row.unit_price) }}</template></el-table-column>
              <el-table-column label="对照的报价" width="140"><template #default="{ row }">{{ d.order.data.quotes?.[row.model] || '无' }}</template></el-table-column>
            </el-table>
            <div v-for="i in d.order.issues" :key="i.id" class="issue" :class="{ done: i.resolved }">
              <el-tag size="small" :type="i.resolved ? 'info' : 'danger'">{{ ROLE_CN[i.role] }}</el-tag> {{ i.title }}<span v-if="i.resolved" class="muted">　已处理（{{ i.resolved_by }}：{{ i.note }}）</span>
            </div>
          </div>
          <template v-if="d.task.type === 'PO_ISSUE' && d.issue">
            <div class="note"><b>{{ d.issue.title }}</b><br />{{ d.issue.detail }}</div>
            <el-input v-model="f.note" placeholder="处理结果，例如：已和客户确认按报价单价执行 / 可行交期是 11 月 20 日" />
            <div class="actions">
              <el-button type="primary" :disabled="!d.task.mine || !f.note" @click="act(() => api.post('/orders/issues/resolve', { ref: d.task.ref_id, note: f.note }), '已处理')">已处理，可以接单</el-button>
              <el-button type="danger" plain :disabled="!d.task.mine || !f.note" @click="act(() => api.post('/orders/issues/resolve', { ref: d.task.ref_id, note: f.note, reject: true }), '已退回')">不能接受，退回销售</el-button>
            </div>
          </template>
          <template v-if="d.task.type === 'ERP_IMPORT'">
            <div class="note">评审已通过。自动写入 ERP 的开关目前是关闭的，请按上面的明细在 ERP 里建销售订单，然后把订单号填回来。</div>
            <el-input v-model="f.erp" placeholder="ERP 销售订单号，例如 SO2610120001" />
            <div class="actions"><el-button type="primary" :disabled="!d.task.mine || !f.erp" @click="act(() => api.post(`/orders/${d.order.id}/erp-order`, { erp_order_no: f.erp }), '已记录，开始起草订单确认书')">已在 ERP 建单</el-button></div>
          </template>
        </template>

        <!-- 只分派 -->
        <template v-if="d.task.type === 'ASSIGN'">
          <div v-if="d.task.payload.note || d.task.payload.summary" class="note">{{ d.task.payload.note || d.task.payload.summary }}</div>
          <QuoteCard v-if="d.quote" :q="d.quote" />
          <template v-if="d.task.status === 'OPEN'">
            <el-input v-model="f.note" placeholder="处理结果（可以不填）" />
            <div class="actions"><el-button type="primary" :disabled="!d.task.mine" @click="act(() => api.post(`/tasks/${d.task.task_id}/done`, { result: f.note }), '已完成')">处理完了</el-button></div>
          </template>
        </template>
        <div v-if="d.task.status !== 'OPEN'" class="muted" style="margin-top: 12px">这个待办已由 {{ d.task.done_by }} 处理：{{ d.task.result }}</div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, h, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { ADOPT_CN, OB_CN, ROLE_CN, TASK_TAG, fmt, money, pct } from '../api/workflow'

/** 报价的背景信息：等级、毛利线、底价、历史成交价；成本构成只有管理层和财务能看到 */
const QuoteCard = props => {
  const q = props.q
  const cell = (label, value) => h('div', null, [h('div', { class: 'num' }, value), h('div', { class: 'muted' }, label)])
  return h('div', { class: 'box' }, [
    h('div', { class: 'box-title' }, [`报价 ${q.quote_no}（第 ${q.version} 版）　${q.model}`, h('span', { class: 'muted' }, `　有效期到 ${q.valid_until}　年用量 ${q.annual_volume || '未给'}`)]),
    h('div', { class: 'nums' }, [
      cell('客户等级', q.level), cell('目标毛利率', pct(q.target_margin)), cell('最低毛利率', pct(q.min_margin)), cell('底价', money(q.floor_price)),
      cell('上次成交价', q.last_deal ? money(q.last_deal.unit_price) : '没有'), cell('近一年成交区间', q.deal_range ? `${money(q.deal_range.low)} ~ ${money(q.deal_range.high)}` : '没有'),
      ...(q.unit_cost != null ? [cell('单位成本', money(q.unit_cost)), cell('其中材料 / 加工', `${money(q.cost.material)} / ${money(q.cost.conversion)}`)] : []),
      ...(q.customer_target_price ? [cell('客户目标价', money(q.customer_target_price))] : [])
    ]),
    q.unit_cost == null ? h('div', { class: 'muted' }, '成本构成只有管理层和财务可见。') : null
  ])
}
QuoteCard.props = ['q']

const status = ref('OPEN')
const tasks = ref([])
const show = ref(false)
const d = ref(null)
const meta = ref({ intents: {} })
const customers = ref([])
const f = reactive({})
let timer = null

const overdue = row => row.due_at && new Date(String(row.due_at).replace(' ', 'T')) < new Date()
const needPrice = computed(() => ['ACCEPT', 'PARTIAL', 'TRADE_TERMS', 'ACCEPT_STRATEGIC'].includes(f.strategy))

async function load() {
  tasks.value = await api.get('/tasks', { params: { status: status.value } })
}

async function open(row) {
  d.value = await api.get(`/tasks/${row.task_id}`)
  const x = d.value
  Object.assign(f, { intents: (x.mail?.intents || []).map(i => i.intent), customer_id: x.mail?.party_type === 'CUSTOMER' ? x.mail.party_id : null, text: x.outbound?.draft || '',
    models: (x.inquiry?.selection || []).map(s => (s.grade !== 'NONE' && s.candidates[0]?.model) || ''), tiers: JSON.parse(JSON.stringify(x.quote?.tiers || [])),
    reason: '', note: '', approver: '', below: false, strategy: '', price: null, condition: '', erp: '',
    decisions: (x.inquiry?.items || []).map((_, i) => {
      const ev = (x.evals || []).find(e => e.item_index === i)
      return ev ? { accept: ev.status === 'DONE' && !!ev.report?.tech?.base_model, model: null } : { accept: false, model: x.inquiry.selection[i]?.candidates?.[0]?.model || null }
    }) })
  show.value = true
}

const CHANGE_CN = { SHAFT: '转轴', WINDING: '绕组', CONNECTOR: '出线/插头', HOUSING: '机壳/安装', MAGNET: '磁钢', BEARING: '轴承', ENCODER: '编码器', PACKAGING: '包装', OTHER: '其他' }
const AGENT_CN = { tech: '技术 Agent', cost: '成本 Agent', lead: '交期 Agent', commercial: '商务 Agent', single: '单 Agent' }
const evalOf = i => (d.value.evals || []).find(e => e.item_index === i)
function showEvidence(ev, id) {
  const e = (ev.trace?.evidence || []).find(x => x.id === id)
  ElMessageBox.alert(h('pre', { style: 'white-space:pre-wrap;font-size:12px;max-height:60vh;overflow:auto' }, e ? `${e.agent} 调用 ${e.tool}\n参数：${JSON.stringify(e.args, null, 1)}\n结果：${JSON.stringify(e.result, null, 1)}` : '找不到这条证据'), `证据 ${id}`, { confirmButtonText: '关闭' })
}

async function preview() {
  const r = await api.post('/quotes/preview', { ref: d.value.task.ref_id, tiers: f.tiers })
  Object.assign(f, { tiers: r.tiers, approver: r.approver, below: r.below_floor })
}

function pick() {
  const o = d.value.negotiation.analysis.options.find(x => x.strategy === f.strategy)
  f.price = o?.price ?? null
}

async function act(fn, message) {
  await fn()
  ElMessage.success(message)
  show.value = false
  load()
}

onMounted(async () => {
  load()
  meta.value = await api.get('/meta')
  customers.value = await api.get('/customers')
  timer = setInterval(() => { if (!show.value) load() }, 8000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; font-size: 12px; }
.spacer { flex: 1; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
.box { border: 1px solid #e5e8ef; border-radius: 6px; padding: 10px 12px; margin-bottom: 12px; }
.box-title { font-weight: 600; font-size: 13px; margin-bottom: 6px; }
.subject { font-size: 13px; margin-bottom: 6px; }
.mail { background: #f6f8fa; border-radius: 6px; padding: 8px 10px; font-size: 13px; white-space: pre-wrap; word-break: break-all; margin: 0 0 6px; font-family: inherit; max-height: 220px; overflow: auto; }
.mail.small { font-size: 12px; max-height: 120px; }
.att { margin-bottom: 6px; }
.note { background: #fdf6ec; color: #8a6d3b; border-left: 3px solid #e6a23c; padding: 6px 10px; margin: 8px 0; font-size: 13px; border-radius: 2px; }
.actions { margin-top: 12px; display: flex; gap: 8px; }
.cand { margin-top: 8px; font-size: 13px; }
.good { color: #529b2e; }
.bad { color: #c45656; }
.nums { display: flex; gap: 22px; flex-wrap: wrap; margin: 6px 0 8px; }
.nums :deep(.num), .num { font-size: 16px; font-weight: 600; color: #2c3e50; }
.options { display: flex; flex-direction: column; gap: 8px; align-items: stretch; margin-top: 10px; }
.options :deep(.el-radio) { height: auto; padding: 8px 12px; margin-right: 0; white-space: normal; align-items: flex-start; }
.why { font-size: 12px; margin-top: 2px; font-weight: normal; }
.issue { font-size: 13px; margin-top: 6px; }
.sub { font-size: 12px; color: #8a8f99; margin: 6px 0 4px; }
.ev { cursor: pointer; margin-right: 2px; }
.agent { font-size: 12px; margin-bottom: 6px; }
.step { margin-left: 12px; }
.detail { margin-top: 8px; }
.issue.done { color: #8a8f99; }
:deep(.el-table__row) { cursor: pointer; }
</style>
