<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Connection, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus-secondary'
import { chatTraceApi, type ChatExecutionTraceItem } from '@/api/chatTrace'
import { formatTimestamp } from '@/utils/date'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const recordIdInput = ref('')
const traceItems = ref<ChatExecutionTraceItem[]>([])
const selectedTraceGroup = ref('')
const detailDialogVisible = ref(false)
const detailTitle = ref('')
const detailContent = ref('')

const stats = reactive({
  total: 0,
  success: 0,
  error: 0,
  running: 0,
})

const traceGroupOptions = computed(() => {
  const groups: { label: string; value: string }[] = []
  const seen = new Set<string>()
  traceItems.value.forEach((item) => {
    if (!item.trace_group || seen.has(item.trace_group)) return
    seen.add(item.trace_group)
    groups.push({
      label: `${item.trace_group.slice(0, 8)}... (${formatDateTime(item.start_time)})`,
      value: item.trace_group,
    })
  })
  return groups
})

const currentTraceItems = computed(() => {
  const activeGroup = selectedTraceGroup.value || traceGroupOptions.value[0]?.value
  return traceItems.value.filter((item) => item.trace_group === activeGroup)
})

function formatDateTime(value?: string | null) {
  if (!value) return '-'
  return formatTimestamp(new Date(value).getTime(), 'YYYY-MM-DD HH:mm:ss')
}

function formatDuration(value?: number | null) {
  if (value === null || value === undefined) return '-'
  if (value < 1000) return `${value}ms`
  return `${(value / 1000).toFixed(2)}s`
}

function getStatusTagType(status: string) {
  if (status === 'success') return 'success'
  if (status === 'error') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

function updateStats(items: ChatExecutionTraceItem[]) {
  stats.total = items.length
  stats.success = items.filter((item) => item.status === 'success').length
  stats.error = items.filter((item) => item.status === 'error').length
  stats.running = items.filter((item) => item.status === 'running').length
}

function openPayload(title: string, payload: unknown) {
  detailTitle.value = title
  detailContent.value =
    payload === null || payload === undefined
      ? '无数据'
      : typeof payload === 'string'
        ? payload
        : JSON.stringify(payload, null, 2)
  detailDialogVisible.value = true
}

async function loadTrace(recordId?: number) {
  const targetRecordId = recordId ?? Number(recordIdInput.value)
  if (!targetRecordId || Number.isNaN(targetRecordId)) {
    ElMessage.warning('请输入有效的 record_id')
    return
  }
  loading.value = true
  try {
    const data = await chatTraceApi.getByRecordId(targetRecordId)
    traceItems.value = data || []
    updateStats(traceItems.value)
    selectedTraceGroup.value = traceGroupOptions.value[0]?.value || ''
    recordIdInput.value = String(targetRecordId)
    router.replace({
      path: '/system/execution-trace',
      query: { recordId: String(targetRecordId) },
    })
    if (!traceItems.value.length) {
      ElMessage.info('该 record_id 暂无执行轨迹')
    }
  } catch {
    traceItems.value = []
    updateStats([])
    ElMessage.error('加载执行轨迹失败')
  } finally {
    loading.value = false
  }
}

function useExample(recordId: number) {
  recordIdInput.value = String(recordId)
  loadTrace(recordId)
}

watch(
  () => route.query.recordId,
  (value) => {
    if (!value) return
    const recordId = Number(value)
    if (!Number.isNaN(recordId)) {
      recordIdInput.value = String(recordId)
    }
  },
  { immediate: true }
)

onMounted(() => {
  const recordId = Number(route.query.recordId)
  if (!Number.isNaN(recordId) && recordId > 0) {
    loadTrace(recordId)
  }
})
</script>

<template>
  <div class="trace-page">
    <div class="hero-panel">
      <div class="hero-panel__main">
        <div class="hero-panel__eyebrow">Execution Trace</div>
        <div class="hero-panel__title">问数执行轨迹</div>
        <div class="hero-panel__desc">
          输入一条 <code>record_id</code>，查看从问题接收、术语检索、模板匹配、SQL 生成、执行到分析输出的完整节点轨迹。
        </div>
      </div>
      <div class="hero-panel__search">
        <el-input
          v-model="recordIdInput"
          placeholder="请输入 record_id，例如 9482"
          clearable
          @keyup.enter="loadTrace()"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-button type="primary" @click="loadTrace()">查询轨迹</el-button>
      </div>
      <div class="hero-panel__examples">
        <span class="hero-panel__examples-label">快捷示例：</span>
        <el-button text @click="useExample(9482)">9482</el-button>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-card__label">总节点数</div>
        <div class="stat-card__value">{{ stats.total }}</div>
      </div>
      <div class="stat-card success">
        <div class="stat-card__label">成功节点</div>
        <div class="stat-card__value">{{ stats.success }}</div>
      </div>
      <div class="stat-card error">
        <div class="stat-card__label">失败节点</div>
        <div class="stat-card__value">{{ stats.error }}</div>
      </div>
      <div class="stat-card running">
        <div class="stat-card__label">运行中节点</div>
        <div class="stat-card__value">{{ stats.running }}</div>
      </div>
    </div>

    <div class="trace-board" v-loading="loading">
      <div class="trace-board__header">
        <div class="trace-board__title">
          <el-icon><Connection /></el-icon>
          <span>轨迹节点</span>
        </div>
        <el-select
          v-if="traceGroupOptions.length > 1"
          v-model="selectedTraceGroup"
          placeholder="选择链路"
          style="width: 320px"
        >
          <el-option
            v-for="item in traceGroupOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </div>

      <el-empty v-if="!currentTraceItems.length && !loading" description="暂无执行轨迹数据" />

      <div v-else class="trace-list">
        <div v-for="item in currentTraceItems" :key="item.id" class="trace-item">
          <div class="trace-item__line" />
          <div class="trace-item__dot" :class="item.status" />
          <div class="trace-item__card">
            <div class="trace-item__top">
              <div>
                <div class="trace-item__name">{{ item.node_name }}</div>
                <div class="trace-item__key">{{ item.node_key }}</div>
              </div>
              <el-tag :type="getStatusTagType(item.status)" effect="dark">
                {{ item.status }}
              </el-tag>
            </div>

            <div class="trace-item__meta">
              <span>开始：{{ formatDateTime(item.start_time) }}</span>
              <span>结束：{{ formatDateTime(item.finish_time) }}</span>
              <span>耗时：{{ formatDuration(item.duration_ms) }}</span>
              <span>record_id：{{ item.record_id }}</span>
            </div>

            <div v-if="item.error_message" class="trace-item__error">
              {{ item.error_message }}
            </div>

            <div class="trace-item__actions">
              <el-button
                v-if="item.input_payload"
                size="small"
                plain
                @click="openPayload(`${item.node_name} - 输入`, item.input_payload)"
              >
                查看输入
              </el-button>
              <el-button
                v-if="item.output_payload"
                size="small"
                plain
                @click="openPayload(`${item.node_name} - 输出`, item.output_payload)"
              >
                查看输出
              </el-button>
              <el-button
                v-if="item.extra_data"
                size="small"
                plain
                @click="openPayload(`${item.node_name} - 附加信息`, item.extra_data)"
              >
                附加信息
              </el-button>
              <el-button
                v-if="item.error_message"
                size="small"
                type="danger"
                plain
                @click="openPayload(`${item.node_name} - 错误信息`, item.error_message)"
              >
                错误详情
              </el-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="detailDialogVisible" :title="detailTitle" width="900px">
      <div class="payload-viewer">
        <pre>{{ detailContent }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped lang="less">
.trace-page {
  padding: 20px;
  background:
    radial-gradient(circle at top right, rgba(42, 124, 255, 0.14), transparent 28%),
    radial-gradient(circle at left bottom, rgba(17, 189, 155, 0.12), transparent 24%),
    #f6f8fc;
  min-height: 100%;
}

.hero-panel {
  padding: 24px 28px;
  border-radius: 24px;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 46%, #0f766e 100%);
  color: #f8fafc;
  box-shadow: 0 18px 42px rgba(15, 23, 42, 0.18);
}

.hero-panel__main {
  max-width: 860px;
}

.hero-panel__eyebrow {
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: rgba(226, 232, 240, 0.7);
  margin-bottom: 10px;
}

.hero-panel__title {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.hero-panel__desc {
  margin-top: 10px;
  line-height: 1.7;
  color: rgba(226, 232, 240, 0.88);
}

.hero-panel__desc code {
  padding: 2px 6px;
  border-radius: 8px;
  background: rgba(148, 163, 184, 0.18);
}

.hero-panel__search {
  margin-top: 20px;
  display: flex;
  gap: 12px;
  max-width: 640px;
}

.hero-panel__examples {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.hero-panel__examples-label {
  color: rgba(226, 232, 240, 0.74);
  font-size: 13px;
}

.stats-grid {
  margin-top: 18px;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.stat-card {
  padding: 18px 20px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid rgba(15, 23, 42, 0.05);
  backdrop-filter: blur(12px);
  box-shadow: 0 10px 30px rgba(148, 163, 184, 0.12);
}

.stat-card.success {
  background: linear-gradient(135deg, rgba(220, 252, 231, 0.92), rgba(240, 253, 244, 0.96));
}

.stat-card.error {
  background: linear-gradient(135deg, rgba(254, 226, 226, 0.92), rgba(255, 241, 242, 0.96));
}

.stat-card.running {
  background: linear-gradient(135deg, rgba(254, 249, 195, 0.95), rgba(255, 251, 235, 0.98));
}

.stat-card__label {
  color: #64748b;
  font-size: 13px;
}

.stat-card__value {
  margin-top: 8px;
  font-size: 30px;
  font-weight: 700;
  color: #0f172a;
}

.trace-board {
  margin-top: 18px;
  padding: 20px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 18px 48px rgba(148, 163, 184, 0.16);
}

.trace-board__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.trace-board__title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
}

.trace-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.trace-item {
  position: relative;
  padding-left: 28px;
}

.trace-item__line {
  position: absolute;
  left: 8px;
  top: 16px;
  bottom: -18px;
  width: 2px;
  background: linear-gradient(180deg, rgba(148, 163, 184, 0.65), rgba(148, 163, 184, 0.08));
}

.trace-item:last-child .trace-item__line {
  display: none;
}

.trace-item__dot {
  position: absolute;
  left: 0;
  top: 16px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 3px solid rgba(255, 255, 255, 0.95);
  box-shadow: 0 0 0 4px rgba(148, 163, 184, 0.14);
  background: #94a3b8;
}

.trace-item__dot.success {
  background: #22c55e;
}

.trace-item__dot.error {
  background: #ef4444;
}

.trace-item__dot.running {
  background: #f59e0b;
}

.trace-item__card {
  padding: 18px 20px;
  border-radius: 18px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.9));
}

.trace-item__top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.trace-item__name {
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
}

.trace-item__key {
  margin-top: 4px;
  color: #64748b;
  font-size: 12px;
  letter-spacing: 0.04em;
}

.trace-item__meta {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: #64748b;
  font-size: 12px;
}

.trace-item__error {
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(254, 226, 226, 0.8);
  color: #b91c1c;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
}

.trace-item__actions {
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.payload-viewer {
  max-height: 70vh;
  overflow: auto;
  padding: 14px;
  border-radius: 14px;
  background: #0f172a;
}

.payload-viewer pre {
  margin: 0;
  color: #e2e8f0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.7;
}

@media (max-width: 1100px) {
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .hero-panel__search {
    max-width: none;
  }
}

@media (max-width: 768px) {
  .trace-page {
    padding: 12px;
  }

  .hero-panel {
    padding: 18px;
    border-radius: 18px;
  }

  .hero-panel__search {
    flex-direction: column;
  }

  .stats-grid {
    grid-template-columns: 1fr;
  }

  .trace-board__header {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
