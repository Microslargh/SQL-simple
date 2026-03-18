<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChatDotRound } from '@element-plus/icons-vue'
import { errorQueryRecordApi, type ErrorQueryRecordItem } from '@/api/errorQueryRecord'
import { formatTimestamp } from '@/utils/date'
import { ElMessage, ElMessageBox } from 'element-plus-secondary'

const router = useRouter()
const tableData = ref<ErrorQueryRecordItem[]>([])
const loading = ref(false)
const filterStatus = ref<string>('') // '' 全部，pending 待解决，resolved 已解决
const filterReason = ref<string>('') // '' 全部，no_result / inaccurate_data / wrong_analysis
const pageInfo = reactive({
  currentPage: 1,
  pageSize: 10,
  total: 0,
})

const reasonLabels: Record<string, string> = {
  no_result: '查询无结果',
  inaccurate_data: '查询到的数据不准确',
  wrong_analysis: '分析过程有误',
}

const statusOptions = [
  { label: '待解决', value: 'pending' },
  { label: '已解决', value: 'resolved' },
]

const filterOptions = [
  { label: '全部', value: '' },
  { label: '待解决', value: 'pending' },
  { label: '已解决', value: 'resolved' },
]

const reasonFilterOptions = [
  { label: '全部', value: '' },
  { label: '查询无结果', value: 'no_result' },
  { label: '查询到的数据不准确', value: 'inaccurate_data' },
  { label: '分析过程有误', value: 'wrong_analysis' },
]

function load() {
  loading.value = true
  const statusParam = filterStatus.value || undefined
  const reasonParam = filterReason.value || undefined
  errorQueryRecordApi
    .pager(pageInfo.currentPage, pageInfo.pageSize, statusParam, reasonParam)
    .then((res: any) => {
      tableData.value = res.items || []
      pageInfo.total = res.total ?? 0
    })
    .catch(() => {
      tableData.value = []
    })
    .finally(() => {
      loading.value = false
    })
}

function onFilterChange() {
  pageInfo.currentPage = 1
  load()
}

function handleSizeChange() {
  pageInfo.currentPage = 1
  load()
}
function handleCurrentChange() {
  load()
}

async function onStatusChange(row: ErrorQueryRecordItem, newStatus: string) {
  if (!row.id || row.status === newStatus) return
  try {
    await errorQueryRecordApi.updateStatus(row.id, newStatus as 'pending' | 'resolved')
    row.status = newStatus
    ElMessage.success('状态已更新')
  } catch {
    ElMessage.error('更新失败')
  }
}

async function handleDelete(row: ErrorQueryRecordItem) {
  if (!row.id) return
  try {
    await ElMessageBox.confirm('确定要删除该条反馈记录吗？', '删除确认', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await errorQueryRecordApi.delete(row.id)
    ElMessage.success('已删除')
    load()
  } catch {
    ElMessage.error('删除失败')
  }
}

function handleViewTrace(row: ErrorQueryRecordItem) {
  if (!row.record_id) {
    ElMessage.warning('该记录缺少 record_id，无法查看轨迹')
    return
  }
  router.push({
    path: '/system/execution-trace',
    query: { recordId: String(row.record_id) },
  })
}

onMounted(() => {
  load()
})
</script>

<template>
  <div class="sqlbot-table-container professional-container">
    <div class="sqlbot-tool">
      <div class="tool-left">
        <el-icon class="page-title-icon"><ChatDotRound /></el-icon>
        <span class="page-title">反馈空间</span>
        <span class="page-desc">用户点踩反馈的问题、SQL 与报错信息，便于运维快速响应与标记处理状态</span>
      </div>
      <div class="tool-right">
        <span class="filter-label">处理状态：</span>
        <el-select
          v-model="filterStatus"
          placeholder="全部"
          clearable
          style="width: 120px"
          @change="onFilterChange"
        >
          <el-option
            v-for="opt in filterOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <span class="filter-label">反馈原因：</span>
        <el-select
          v-model="filterReason"
          placeholder="全部"
          clearable
          style="width: 160px"
          @change="onFilterChange"
        >
          <el-option
            v-for="opt in reasonFilterOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </div>
    </div>
    <div class="sqlbot-table">
      <el-table v-loading="loading" :data="tableData" style="width: 100%">
        <el-table-column prop="create_time" label="时间" width="170">
          <template #default="{ row }">
            {{
              row.create_time
                ? formatTimestamp(
                    typeof row.create_time === 'string' ? new Date(row.create_time).getTime() : row.create_time,
                    'YYYY-MM-DD HH:mm:ss'
                  )
                : '-'
            }}
          </template>
        </el-table-column>
        <el-table-column prop="status" label="处理状态" width="140" align="center">
          <template #default="{ row }">
            <el-select
              :model-value="row.status || 'pending'"
              size="small"
              style="width: 100px"
              @update:model-value="(v: string) => onStatusChange(row, v)"
            >
              <el-option
                v-for="opt in statusOptions"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column prop="feedback_reason" label="反馈原因" width="160">
          <template #default="{ row }">
            {{ reasonLabels[row.feedback_reason] || row.feedback_reason || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="question" label="用户问题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="sql" label="生成 SQL" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <pre class="cell-pre">{{ row.feedback_reason === 'wrong_analysis' ? '-' : (row.sql || '-') }}</pre>
          </template>
        </el-table-column>
        <el-table-column prop="analysis_text" label="数据分析内容" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <pre class="cell-pre">{{ row.feedback_reason === 'wrong_analysis' ? (row.analysis_text || '-') : '-' }}</pre>
          </template>
        </el-table-column>
        <el-table-column prop="error_message" label="报错信息" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <pre class="cell-pre error-msg">{{ row.error_message || '-' }}</pre>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="center" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="handleViewTrace(row)">轨迹</el-button>
            <el-button type="danger" link size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pagination-container">
        <el-pagination
          v-model:current-page="pageInfo.currentPage"
          v-model:page-size="pageInfo.pageSize"
          :page-sizes="[10, 20, 50]"
          :background="true"
          layout="total, sizes, prev, pager, next, jumper"
          :total="pageInfo.total"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </div>
  </div>
</template>

<style scoped lang="less">
.page-title {
  font-weight: 600;
  margin-right: 12px;
}
.page-title-icon {
  margin-right: 8px;
  vertical-align: middle;
  font-size: 20px;
}
.page-desc {
  color: #909399;
  font-size: 13px;
}
.tool-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-label {
  font-size: 13px;
  color: #606266;
}
.cell-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  max-height: 80px;
  overflow: auto;
  &.error-msg {
    color: var(--el-color-danger);
  }
}
.pagination-container {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
