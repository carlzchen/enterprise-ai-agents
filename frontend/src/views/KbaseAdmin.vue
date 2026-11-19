<template>
  <div class="page">
    <div class="page-card">
      <div class="stats">
        <div v-for="c in cards" :key="c.label" class="stat">
          <div class="num" :class="{ warn: c.warn }">{{ c.value }}</div>
          <div class="muted">{{ c.label }}</div>
        </div>
        <span class="spacer" />
        <el-dropdown trigger="click" @command="startBatch">
          <el-button type="primary">批量入库<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="scan">扫描共享目录（只入新增和有变化的）</el-dropdown-item>
              <el-dropdown-item command="plm">同步 PLM 现行文档</el-dropdown-item>
              <el-dropdown-item command="zip">上传压缩包</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <input ref="zipInput" type="file" accept=".zip" style="display: none" @change="uploadZip" />
        <el-button @click="openUpload">单个补录</el-button>
        <el-button :loading="doing === 'kd'" @click="run('kd', '/kingdee/sync', r => `金蝶 EAS 同步完成：物料 ${r.materials} 条，BOM ${r.bom_lines} 行`)">同步金蝶 EAS 物料和 BOM</el-button>
        <el-button v-if="isAdmin" :loading="doing === 'rc'" @click="run('rc', '/reconcile', r => `对账完成：${r.documents} 份文档，一致 ${r.consistent}，已修复 ${r.repaired}，异常 ${r.broken.length}`)">立即对账</el-button>
        <el-button :loading="doing === 'kg'" @click="run('kg', '/kg/rebuild', r => `图谱已重建：${r.nodes ?? '-'} 个节点，${r.edges ?? '-'} 条关系`)">重建知识图谱</el-button>
      </div>
      <el-tabs v-model="tab" @tab-change="load">
        <el-tab-pane label="文档" name="docs">
          <el-table :data="docs" size="small" border stripe height="calc(100vh - 320px)" @row-click="openDoc">
            <el-table-column prop="title" label="文档" min-width="220" show-overflow-tooltip />
            <el-table-column label="类型" width="100"><template #default="{ row }">{{ DOC_TYPES[row.doc_type] || row.doc_type }}</template></el-table-column>
            <el-table-column label="来源" width="80"><template #default="{ row }">{{ { PLM: 'PLM', UPLOAD: '上传', FILE: '文件', FOLDER: '共享目录' }[row.source] || row.source }}</template></el-table-column>
            <el-table-column label="解析方式" width="110" prop="parsed_by" />
            <el-table-column label="密级" width="70"><template #default="{ row }">
              <el-tag size="small" :type="row.security_level === 'PUBLIC' ? 'success' : row.security_level === 'CONFIDENTIAL' ? 'danger' : 'warning'">{{ LEVELS[row.security_level] || row.security_level }}</el-tag>
            </template></el-table-column>
            <el-table-column label="可见范围" min-width="160" show-overflow-tooltip><template #default="{ row }">{{ aclText(row.acl_groups) }}</template></el-table-column>
            <el-table-column prop="active_version" label="生效版本" width="80" align="center" />
            <el-table-column prop="versions" label="版本数" width="70" align="right" />
            <el-table-column prop="chunk_count" label="切片" width="60" align="right" />
            <el-table-column prop="param_count" label="参数" width="60" align="right" />
            <el-table-column label="告警" width="60" align="center"><template #default="{ row }"><el-tag v-if="row.alerts" size="small" type="danger">{{ row.alerts }}</el-tag></template></el-table-column>
            <el-table-column label="生效时间" width="150"><template #default="{ row }">{{ fmtTime(row.activated_at) }}</template></el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="批量入库" name="batches">
          <div class="bar">
            <el-tag :type="queue.mq ? 'success' : 'danger'" effect="plain">消息队列{{ queue.mq ? '正常' : '不可用（已降级为进程内执行）' }}</el-tag>
            <span class="muted">排队 {{ queue.depth.ready ?? '-' }} · 等待重试 {{ (queue.depth['retry.30'] || 0) + (queue.depth['retry.120'] || 0) + (queue.depth['retry.600'] || 0) }} · 死信 {{ queue.depth.dlq ?? '-' }}</span>
            <span class="muted">共享目录：{{ queue.inbox }}</span>
          </div>
          <el-table :data="batches" size="small" border stripe height="calc(100vh - 360px)" @row-click="openBatch">
            <el-table-column prop="title" label="批次" min-width="200" show-overflow-tooltip />
            <el-table-column label="来源" width="90"><template #default="{ row }">{{ BATCH_SRC[row.source] || row.source }}</template></el-table-column>
            <el-table-column prop="trigger_by" label="触发人" width="90" />
            <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag size="small" :type="BATCH[row.status]?.type">{{ BATCH[row.status]?.text || row.status }}</el-tag></template></el-table-column>
            <el-table-column label="进度" min-width="220"><template #default="{ row }">
              <el-progress :percentage="row.total ? Math.round(100 * (row.total - row.pending) / row.total) : 0" :status="row.failed ? 'exception' : row.pending ? '' : 'success'" :stroke-width="10" />
            </template></el-table-column>
            <el-table-column prop="total" label="文件" width="60" align="right" />
            <el-table-column prop="done" label="成功" width="60" align="right" />
            <el-table-column prop="skipped" label="跳过" width="60" align="right" />
            <el-table-column label="失败" width="60" align="right"><template #default="{ row }"><span :class="{ bad: row.failed }">{{ row.failed }}</span></template></el-table-column>
            <el-table-column label="开始时间" width="150"><template #default="{ row }">{{ fmtTime(row.started_at) }}</template></el-table-column>
            <el-table-column label="操作" width="210"><template #default="{ row }">
              <el-button v-if="row.status === 'RUNNING'" link type="warning" size="small" @click.stop="batchAct(row, 'pause')">暂停</el-button>
              <el-button v-if="row.status === 'PAUSED'" link type="primary" size="small" @click.stop="batchAct(row, 'resume')">继续</el-button>
              <el-button v-if="['RUNNING', 'PAUSED'].includes(row.status)" link type="danger" size="small" @click.stop="batchAct(row, 'cancel')">取消</el-button>
              <el-button v-if="row.failed && !['RUNNING'].includes(row.status)" link type="primary" size="small" @click.stop="batchAct(row, 'retry-failed')">重试失败项</el-button>
            </template></el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="定时任务" name="schedules">
          <el-table :data="schedules.jobs" size="small" border stripe>
            <el-table-column prop="title" label="任务" min-width="200" />
            <el-table-column prop="plan" label="执行时间" width="120" />
            <el-table-column label="启用" width="70"><template #default="{ row }"><el-switch v-model="row.on" size="small" :disabled="!isAdmin" @change="v => toggleJob(row, v)" /></template></el-table-column>
            <el-table-column label="上次运行" width="150"><template #default="{ row }">{{ fmtTime(row.last_run_at) }}</template></el-table-column>
            <el-table-column label="结果" width="80"><template #default="{ row }"><el-tag v-if="row.last_status" size="small" :type="row.last_status === 'OK' ? 'success' : 'danger'">{{ row.last_status === 'OK' ? '成功' : '失败' }}</el-tag></template></el-table-column>
            <el-table-column prop="last_message" label="上次结果说明" min-width="320" show-overflow-tooltip />
            <el-table-column label="耗时" width="80" align="right"><template #default="{ row }">{{ row.last_ms != null ? (row.last_ms / 1000).toFixed(1) + ' 秒' : '' }}</template></el-table-column>
            <el-table-column label="下次运行" width="150"><template #default="{ row }">{{ fmtTime(row.next_run_at) }}</template></el-table-column>
            <el-table-column label="操作" width="90"><template #default="{ row }"><el-button link type="primary" size="small" @click="runJob(row)">立即执行</el-button></template></el-table-column>
          </el-table>
          <h4>最近的运行记录</h4>
          <el-table :data="schedules.runs" size="small" border stripe height="calc(100vh - 670px)" style="min-height: 160px">
            <el-table-column label="时间" width="150"><template #default="{ row }">{{ fmtTime(row.started_at) }}</template></el-table-column>
            <el-table-column prop="title" label="任务" width="200" />
            <el-table-column prop="trigger_by" label="触发" width="80" />
            <el-table-column label="结果" width="70"><template #default="{ row }"><el-tag size="small" :type="row.status === 'OK' ? 'success' : 'danger'">{{ row.status === 'OK' ? '成功' : '失败' }}</el-tag></template></el-table-column>
            <el-table-column prop="message" label="说明" min-width="400" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>
        <el-tab-pane name="ops">
          <template #label>运行告警<el-badge v-if="openAlerts" :value="openAlerts" class="badge" /></template>
          <div class="deps">
            <div v-for="d in ops.dependencies" :key="d.name" class="dep" :class="{ down: !d.ok }">
              <div class="dep-name"><span class="dot" />{{ d.name }}</div>
              <div class="muted">{{ d.ok ? '正常' : d.degrade || '不可用' }}</div>
            </div>
            <div v-for="b in ops.breakers" :key="b.name" class="dep" :class="{ down: b.state === 'OPEN' }">
              <div class="dep-name"><span class="dot" />熔断器 · {{ b.title }}</div>
              <div class="muted">{{ { CLOSED: '关闭（正常）', OPEN: '打开（走降级）', HALF_OPEN: '试探中' }[b.state] }}</div>
            </div>
          </div>
          <div class="bar muted">
            <span v-for="(v, k) in ops.limits" :key="k">{{ k }}：{{ v }}</span>
            <span>飞书推送：{{ ops.feishu ? '已配置' : '未配置' }}</span><span>邮件：{{ ops.smtp ? '已配置' : '未配置' }}</span>
          </div>
          <el-table :data="opsAlerts" size="small" border stripe height="calc(100vh - 500px)">
            <el-table-column type="expand"><template #default="{ row }"><pre class="body">{{ row.detail }}</pre></template></el-table-column>
            <el-table-column label="级别" width="70"><template #default="{ row }"><el-tag size="small" :type="{ P1: 'danger', P2: 'warning', P3: 'info' }[row.level]">{{ { P1: '严重', P2: '重要', P3: '提示' }[row.level] }}</el-tag></template></el-table-column>
            <el-table-column prop="title" label="告警" min-width="300" show-overflow-tooltip />
            <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag size="small" :type="{ OPEN: 'danger', ACK: 'warning', CLOSED: 'info' }[row.status]">{{ { OPEN: '未处理', ACK: '已确认', CLOSED: '已关闭' }[row.status] }}</el-tag></template></el-table-column>
            <el-table-column prop="hits" label="次数" width="60" align="right" />
            <el-table-column label="首次" width="150"><template #default="{ row }">{{ fmtTime(row.first_at) }}</template></el-table-column>
            <el-table-column label="最近" width="150"><template #default="{ row }">{{ fmtTime(row.last_at) }}</template></el-table-column>
            <el-table-column prop="notify_result" label="通知" width="130" show-overflow-tooltip />
            <el-table-column label="操作" width="110"><template #default="{ row }">
              <el-button v-if="row.status === 'OPEN'" link type="primary" size="small" @click="alertAct(row, 'ack')">确认</el-button>
              <el-button v-if="row.status !== 'CLOSED'" link size="small" @click="alertAct(row, 'close')">关闭</el-button>
            </template></el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="入库任务" name="jobs">
          <el-table :data="jobs" size="small" border stripe height="calc(100vh - 320px)">
            <el-table-column prop="job_id" label="任务号" width="190" />
            <el-table-column prop="file_name" label="文件" min-width="200" show-overflow-tooltip />
            <el-table-column prop="doc_key" label="文档编号" width="170" show-overflow-tooltip />
            <el-table-column prop="version" label="版本" width="60" />
            <el-table-column label="状态" width="90"><template #default="{ row }">
              <el-tag size="small" :type="JOB[row.status]?.type">{{ JOB[row.status]?.text || row.status }}</el-tag>
            </template></el-table-column>
            <el-table-column prop="stage" label="停在哪一步" width="150" />
            <el-table-column prop="attempts" label="次数" width="60" align="right" />
            <el-table-column label="下次重试" width="150"><template #default="{ row }">{{ row.status === 'RETRY' ? fmtTime(row.next_retry_at) : '' }}</template></el-table-column>
            <el-table-column prop="error" label="原因" min-width="260" show-overflow-tooltip />
            <el-table-column label="开始时间" width="150"><template #default="{ row }">{{ fmtTime(row.started_at) }}</template></el-table-column>
            <el-table-column label="操作" width="80"><template #default="{ row }">
              <el-button v-if="['FAILED', 'RETRY'].includes(row.status)" link type="primary" size="small" @click="retry(row)">重试</el-button>
            </template></el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="数据告警" name="alerts">
          <el-table :data="alerts" size="small" border stripe height="calc(100vh - 320px)">
            <el-table-column label="时间" width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
            <el-table-column prop="title" label="文档" min-width="200" show-overflow-tooltip />
            <el-table-column prop="version" label="版本" width="60" />
            <el-table-column prop="kind" label="类型" width="150" />
            <el-table-column prop="message" label="内容" min-width="420" show-overflow-tooltip />
            <el-table-column label="状态" width="80"><template #default="{ row }"><el-tag size="small" :type="row.status === 'OPEN' ? 'danger' : 'info'">{{ row.status === 'OPEN' ? '待处理' : '已关闭' }}</el-tag></template></el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="通知邮件" name="outbox">
          <el-table :data="outbox" size="small" border stripe height="calc(100vh - 320px)">
            <el-table-column type="expand"><template #default="{ row }"><pre class="body">{{ row.body }}</pre></template></el-table-column>
            <el-table-column label="时间" width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
            <el-table-column prop="to_email" label="收件人" width="240" show-overflow-tooltip />
            <el-table-column prop="subject" label="标题" min-width="320" show-overflow-tooltip />
            <el-table-column label="状态" width="90"><template #default="{ row }">
              <el-tag size="small" :type="row.status === 'SENT' ? 'success' : row.status === 'FAILED' ? 'danger' : 'warning'">{{ { SENT: '已发送', FAILED: '发送失败', PENDING: '待发送' }[row.status] }}</el-tag>
            </template></el-table-column>
            <el-table-column prop="tries" label="次数" width="60" align="right" />
            <el-table-column prop="last_error" label="失败原因" min-width="200" show-overflow-tooltip />
          </el-table>
          <div v-if="stats && !stats.smtp" class="muted hint">当前没有配置邮件服务器（SMTP_HOST），邮件只进发件箱、不会真的发出去。</div>
        </el-tab-pane>
        <el-tab-pane label="Bad Case 池" name="bad">
          <el-table :data="bad" size="small" border stripe height="calc(100vh - 320px)">
            <el-table-column type="expand"><template #default="{ row }">
              <div class="case">
                <p><b>当时的回答：</b>{{ row.answer }}</p>
                <p v-if="row.correct_answer"><b>用户给的正确答案：</b>{{ row.correct_answer }}</p>
                <p class="muted">引用的文档版本：{{ row.doc_versions }}</p>
                <p class="muted">引用的切片：{{ row.chunk_ids }}</p>
                <p v-if="row.trace_url"><a :href="row.trace_url" target="_blank">查看这次回答的完整链路（Langfuse）</a></p>
              </div>
            </template></el-table-column>
            <el-table-column label="时间" width="150"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
            <el-table-column prop="username" label="提问人" width="90" />
            <el-table-column prop="question" label="问题" min-width="280" show-overflow-tooltip />
            <el-table-column prop="route" label="路线" width="80" />
            <el-table-column prop="reason" label="问题类型" width="110" />
            <el-table-column prop="comment" label="补充说明" min-width="200" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog v-model="up.show" title="上传资料" width="560px">
      <el-form label-width="96px">
        <el-form-item label="文件" required>
          <el-upload :auto-upload="false" :limit="1" :on-change="f => (up.file = f.raw)" :on-remove="() => (up.file = null)"
                     accept=".pdf,.md,.docx,.pptx,.xlsx,.csv,.dxf,.dwg">
            <el-button>选择文件</el-button>
            <template #tip><div class="muted">支持 PDF、Word、PPT、Markdown、Excel/CSV 选型表、DXF/DWG 图纸，单个文件不超过 50 MB</div></template>
          </el-upload>
        </el-form-item>
        <el-form-item label="文档编号" required><el-input v-model="up.doc_key" placeholder="同一份文档的不同版本用同一个编号，如 DS-KL-SV-60-200W" /></el-form-item>
        <el-form-item label="版本" required><el-input v-model="up.version" placeholder="如 A、B 或 1、2" style="width: 160px" /></el-form-item>
        <el-form-item label="标题" required><el-input v-model="up.title" /></el-form-item>
        <el-form-item label="类型" required>
          <el-select v-model="up.doc_type" style="width: 200px"><el-option v-for="(v, k) in DOC_TYPES" :key="k" :value="k" :label="v" /></el-select>
        </el-form-item>
        <el-form-item label="密级" required>
          <el-radio-group v-model="up.security_level"><el-radio v-for="(v, k) in LEVELS" :key="k" :value="k">{{ v }}</el-radio></el-radio-group>
        </el-form-item>
        <el-form-item label="可见范围" required>
          <el-select v-model="up.acl" multiple style="width: 100%" placeholder="哪些角色可以查到这份资料">
            <el-option v-for="(v, k) in ROLES" :key="k" :value="k" :label="v" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联产品"><el-input v-model="up.product_code" placeholder="产品型号，可以不填" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="up.show = false">取消</el-button>
        <el-button type="primary" :loading="up.busy" @click="submitUpload">开始入库</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="batch.show" :title="batch.data?.title" size="760px">
      <template v-if="batch.data">
        <el-descriptions :column="3" size="small" border>
          <el-descriptions-item label="批次号">{{ batch.data.batch_id }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ BATCH_SRC[batch.data.source] }}</el-descriptions-item>
          <el-descriptions-item label="执行方式">{{ batch.data.mode === 'MQ' ? '消息队列' : '进程内（队列不可用时的降级）' }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ BATCH[batch.data.status]?.text }}</el-descriptions-item>
          <el-descriptions-item label="成功 / 跳过 / 失败">{{ batch.data.done }} / {{ batch.data.skipped }} / {{ batch.data.failed }}</el-descriptions-item>
          <el-descriptions-item label="还没处理">{{ batch.data.pending }}</el-descriptions-item>
        </el-descriptions>
        <div v-if="batch.data.pause_reason" class="alert" style="margin-top: 8px">暂停原因：{{ batch.data.pause_reason }}</div>
        <h4>文件明细</h4>
        <el-table :data="batch.data.jobs" size="small" border max-height="calc(100vh - 330px)">
          <el-table-column prop="file_name" label="文件" min-width="200" show-overflow-tooltip />
          <el-table-column prop="version" label="版本" width="60" />
          <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag size="small" :type="JOB[row.status]?.type">{{ JOB[row.status]?.text || row.status }}</el-tag></template></el-table-column>
          <el-table-column prop="attempts" label="次数" width="55" align="right" />
          <el-table-column prop="error" label="原因" min-width="260" show-overflow-tooltip />
        </el-table>
      </template>
    </el-drawer>

    <el-drawer v-model="detail.show" :title="detail.doc?.title" size="640px">
      <template v-if="detail.doc">
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="文档编号">{{ detail.doc.doc_key }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ DOC_TYPES[detail.doc.doc_type] || detail.doc.doc_type }}</el-descriptions-item>
          <el-descriptions-item label="密级">{{ LEVELS[detail.doc.security_level] }}</el-descriptions-item>
          <el-descriptions-item label="可见范围">{{ aclText(detail.doc.acl_groups) }}</el-descriptions-item>
          <el-descriptions-item label="负责人">{{ detail.doc.owner_email }}</el-descriptions-item>
          <el-descriptions-item label="生效版本">{{ detail.doc.active_version }}</el-descriptions-item>
        </el-descriptions>
        <h4>版本记录
          <el-button v-if="detail.doc.versions.some(v => v.status === 'SUPERSEDED')" size="small" type="warning" plain style="margin-left: 8px" @click="rollback">回滚到上一个版本</el-button>
        </h4>
        <el-table :data="detail.doc.versions" size="small" border>
          <el-table-column prop="version" label="版本" width="60" />
          <el-table-column label="状态" width="90"><template #default="{ row }">
            <el-tag size="small" :type="VER[row.status]?.type">{{ VER[row.status]?.text || row.status }}</el-tag>
          </template></el-table-column>
          <el-table-column prop="file_name" label="文件" min-width="160" show-overflow-tooltip />
          <el-table-column prop="parsed_by" label="解析" width="100" />
          <el-table-column prop="chunk_count" label="切片" width="60" align="right" />
          <el-table-column label="生效时间" width="150"><template #default="{ row }">{{ fmtTime(row.activated_at) }}</template></el-table-column>
        </el-table>
        <template v-if="detail.doc.alerts.length">
          <h4>告警</h4>
          <div v-for="(a, i) in detail.doc.alerts" :key="i" class="alert">版本 {{ a.version }} · {{ a.kind }}：{{ a.message }}</div>
        </template>
        <h4>生效版本的章节</h4>
        <el-table :data="detail.doc.sections" size="small" border max-height="320">
          <el-table-column prop="seq" label="#" width="50" />
          <el-table-column prop="title" label="章节" min-width="260" show-overflow-tooltip />
          <el-table-column prop="page" label="页" width="60" />
          <el-table-column prop="chars" label="字数" width="70" align="right" />
        </el-table>
        <el-button v-if="isAdmin" type="danger" plain style="margin-top: 16px" @click="remove">删除这份文档（全部版本）</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
import { auth } from '../api/auth'
import kbase, { DOC_TYPES, LEVELS, ROLES, fmtTime } from '../api/kbase'

const JOB = { DONE: { text: '完成', type: 'success' }, SKIPPED: { text: '跳过', type: 'info' }, FAILED: { text: '失败', type: 'danger' }, RETRY: { text: '等待重试', type: 'warning' }, RUNNING: { text: '处理中', type: '' }, QUEUED: { text: '排队中', type: 'info' }, CANCELLED: { text: '已取消', type: 'info' } }
const BATCH = { RUNNING: { text: '进行中', type: '' }, PAUSED: { text: '已暂停', type: 'warning' }, DONE: { text: '全部完成', type: 'success' }, PARTIAL: { text: '有失败项', type: 'danger' }, CANCELLED: { text: '已取消', type: 'info' } }
const BATCH_SRC = { INBOX: '共享目录', PLM: 'PLM', ZIP: '压缩包', MANUAL: '手动' }
const VER = { ACTIVE: { text: '生效中', type: 'success' }, SUPERSEDED: { text: '已被替换', type: 'info' }, STAGED: { text: '暂存', type: 'warning' }, FAILED: { text: '失败', type: 'danger' } }

const isAdmin = computed(() => auth.user?.role === 'ADMIN')
const tab = ref('docs')
const stats = ref(null)
const docs = ref([])
const jobs = ref([])
const alerts = ref([])
const outbox = ref([])
const bad = ref([])
const doing = ref('')
const batches = ref([])
const queue = ref({ mq: true, depth: {}, inbox: '' })
const schedules = ref({ jobs: [], runs: [] })
const ops = ref({ dependencies: [], breakers: [], limits: {} })
const opsAlerts = ref([])
const openAlerts = computed(() => opsAlerts.value.filter(a => a.status === 'OPEN').length)
const batch = reactive({ show: false, data: null })
const zipInput = ref()
const up = reactive({ show: false, busy: false, file: null, doc_key: '', version: '', title: '', doc_type: 'DATASHEET', security_level: '', acl: [], product_code: '' })
const detail = reactive({ show: false, doc: null })
let timer = null

const aclText = s => String(s || '').split(',').filter(Boolean).map(k => ROLES[k] || k).join('、')
const cards = computed(() => {
  const s = stats.value || {}
  return [
    { label: '生效文档', value: s.documents ?? '-' }, { label: '切片', value: s.chunks ?? '-' }, { label: '结构化参数', value: s.params ?? '-' },
    { label: '金蝶 EAS 物料', value: s.kd_materials ?? '-' }, { label: '待处理告警', value: s.alerts ?? '-', warn: s.alerts > 0 },
    { label: '失败任务', value: s.failed_jobs ?? '-', warn: s.failed_jobs > 0 }, { label: 'Bad Case', value: s.bad_cases ?? '-' },
    { label: '知识图谱', value: s.kg ? '已连接' : '未启动', warn: s.kg === false }
  ]
})

async function load() {
  stats.value = await kbase.get('/stats', { silent: true }).catch(() => null)
  if (tab.value === 'docs') docs.value = await kbase.get('/documents')
  else if (tab.value === 'batches') {
    const r = await kbase.get('/batches')
    batches.value = r.batches
    queue.value = { mq: r.mq, depth: r.queue, inbox: r.inbox }
    if (batch.show && batch.data) batch.data = await kbase.get(`/batches/${batch.data.batch_id}`)
  } else if (tab.value === 'schedules') {
    const r = await kbase.get('/schedules')
    r.jobs.forEach(j => { j.on = !!j.enabled })
    schedules.value = r
  } else if (tab.value === 'ops') {
    ops.value = await kbase.get('/ops/status')
    opsAlerts.value = await kbase.get('/ops/alerts')
  } else if (tab.value === 'jobs') jobs.value = await kbase.get('/jobs')
  else if (tab.value === 'alerts') alerts.value = await kbase.get('/alerts')
  else if (tab.value === 'outbox') outbox.value = await kbase.get('/outbox')
  else if (tab.value === 'bad') bad.value = await kbase.get('/badcases')
}

async function run(key, url, message) {
  doing.value = key
  try {
    ElMessage.success(message(await kbase.post(url)))
    load()
  } finally {
    doing.value = ''
  }
}

async function startBatch(kind) {
  if (kind === 'zip') return zipInput.value.click()
  const r = await kbase.post(kind === 'scan' ? '/batches/scan' : '/batches/plm')
  batchStarted(r)
}

async function uploadZip(e) {
  const file = e.target.files[0]
  e.target.value = ''
  if (!file) return
  const fd = new FormData()
  fd.append('file', file)
  batchStarted(await kbase.post('/batches/zip', fd))
}

function batchStarted(r) {
  if (r.batch_id) ElMessage.success(`已创建批次，共 ${r.total} 个文件` + (r.unchanged ? `（另有 ${r.unchanged} 个没有变化，已跳过）` : ''))
  else ElMessage.info(r.message || (r.scanned != null ? `扫描了 ${r.scanned} 个文件，都没有变化` : '没有需要入库的文件'))
  if (r.ignored?.length) ElMessage.warning(`${r.ignored.length} 个文件不符合命名约定，没有入库：${r.ignored[0]}`)
  tab.value = 'batches'
  load()
}

async function openBatch(row) {
  batch.data = await kbase.get(`/batches/${row.batch_id}`)
  batch.show = true
}

async function batchAct(row, action) {
  const names = { pause: '暂停', resume: '继续', cancel: '取消', 'retry-failed': '重试失败项' }
  if (action === 'cancel') await ElMessageBox.confirm(`取消批次「${row.title}」？还没处理的文件不再入库，已经入库的不受影响。`, '确认', { type: 'warning' })
  const r = await kbase.post(`/batches/${row.batch_id}/${action}`)
  ElMessage.success(action === 'retry-failed' ? `已把 ${r.requeued} 个失败的文件重新排队` : `已${names[action]}`)
  load()
}

async function runJob(row) {
  const r = await kbase.post(`/schedules/${row.name}/run`)
  ElMessage.success(r.message)
  setTimeout(load, 3000)
}

async function toggleJob(row, on) {
  await kbase.put(`/schedules/${row.name}`, { enabled: on })
  ElMessage.success(on ? '已启用' : '已停用')
}

async function alertAct(row, action) {
  await kbase.post(`/ops/alerts/${row.id}/${action}`)
  load()
}

function openUpload() {
  Object.assign(up, { show: true, file: null, doc_key: '', version: '', title: '', doc_type: 'DATASHEET', security_level: '', acl: [], product_code: '' })
}

async function submitUpload() {
  if (!up.file || !up.doc_key || !up.version || !up.title) return ElMessage.warning('文件、文档编号、版本、标题都要填')
  if (!up.security_level || !up.acl.length) return ElMessage.warning('密级和可见范围必须选，不选不能入库')
  const fd = new FormData()
  fd.append('file', up.file)
  for (const k of ['doc_key', 'version', 'title', 'doc_type', 'security_level', 'product_code']) fd.append(k, up[k])
  fd.append('acl_groups', up.acl.join(','))
  up.busy = true
  try {
    const r = await kbase.post('/documents/upload', fd)
    ElMessage.success(`${r.message}，任务号 ${r.job_id}`)
    up.show = false
    tab.value = 'jobs'
    load()
  } finally {
    up.busy = false
  }
}

async function retry(row) {
  await kbase.post(`/jobs/${row.job_id}/retry`)
  ElMessage.success('已重新开始')
  load()
}

async function openDoc(row) {
  detail.doc = await kbase.get(`/documents/${row.doc_key}`)
  detail.show = true
}

async function rollback() {
  await ElMessageBox.confirm(`把「${detail.doc.title}」回滚到上一个版本？当前版本会停止被检索。`, '确认回滚', { type: 'warning' })
  const r = await kbase.post(`/documents/${detail.doc.doc_key}/rollback`)
  ElMessage.success(`已回滚，生效版本是 ${r.active_version}`)
  detail.doc = await kbase.get(`/documents/${detail.doc.doc_key}`)
  load()
}

async function remove() {
  await ElMessageBox.confirm(`删除「${detail.doc.title}」的全部版本、切片和参数？这个操作不能撤销。`, '确认删除', { type: 'warning' })
  await kbase.delete(`/documents/${detail.doc.doc_key}`)
  ElMessage.success('已删除')
  detail.show = false
  load()
}

onMounted(async () => {
  load()
  opsAlerts.value = await kbase.get('/ops/alerts', { silent: true }).catch(() => [])
  timer = setInterval(() => { if (['jobs', 'batches'].includes(tab.value)) load() }, 3000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.stats { display: flex; align-items: center; gap: 22px; flex-wrap: wrap; margin-bottom: 8px; }
.stat { text-align: center; }
.num { font-size: 20px; font-weight: 600; color: #2c3e50; }
.num.warn { color: #c45656; }
.spacer { flex: 1; }
.hint { margin-top: 8px; font-size: 12px; }
.body { margin: 0; padding: 8px 16px; white-space: pre-wrap; font-family: inherit; font-size: 12px; }
.case { padding: 4px 16px; font-size: 13px; }
.alert { background: #fef0f0; color: #c45656; padding: 4px 8px; border-radius: 4px; font-size: 12px; margin-bottom: 4px; }
h4 { margin: 16px 0 8px; }
:deep(.el-table__row) { cursor: pointer; }
.bar { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; font-size: 12px; }
.bad { color: #c45656; font-weight: 600; }
.badge { margin-left: 4px; }
.deps { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.dep { border: 1px solid #e1f3d8; background: #f0f9eb; border-radius: 6px; padding: 6px 12px; font-size: 12px; min-width: 150px; }
.dep.down { border-color: #fde2e2; background: #fef0f0; }
.dep-name { font-size: 13px; color: #2c3e50; display: flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #67c23a; display: inline-block; }
.dep.down .dot { background: #f56c6c; }
</style>
