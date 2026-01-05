<template>
  <div class="process-step-container">
    <div
      class="process-step"
      :class="{
        'step-pending': stepStatus === 'pending',
        'step-processing': stepStatus === 'processing',
        'step-completed': stepStatus === 'completed',
        'step-error': stepStatus === 'error',
      }"
    >
      <div class="step-header" @click="toggleExpand">
        <div class="step-icon">
          <el-icon v-if="stepStatus === 'pending'" class="step-icon-pending">
            <Clock />
          </el-icon>
          <el-icon v-else-if="stepStatus === 'processing'" class="step-icon-processing">
            <Loading />
          </el-icon>
          <el-icon v-else-if="stepStatus === 'completed'" class="step-icon-completed">
            <CircleCheck />
          </el-icon>
          <el-icon v-else-if="stepStatus === 'error'" class="step-icon-error">
            <CircleClose />
          </el-icon>
        </div>
        <div class="step-info">
          <div class="step-name">{{ stepName }}</div>
          <div class="step-description" v-if="description">{{ description }}</div>
        </div>
        <div class="step-action">
          <el-icon v-if="hasResult && stepStatus === 'completed'" class="expand-icon">
            <ArrowDown v-if="!expanded" />
            <ArrowUp v-else />
          </el-icon>
        </div>
      </div>
      <div v-if="expanded && hasResult" class="step-result">
        <div v-if="result.count !== undefined" class="result-item">
          <span class="result-label">检索数量：</span>
          <span class="result-value">{{ result.count }}</span>
        </div>
        <div v-if="result.items && result.items.length > 0" class="result-items">
          <div class="result-label">检索结果：</div>
          <div class="items-list">
            <div v-for="(item, index) in result.items" :key="index" class="item-tag">
              <template v-if="item.words && Array.isArray(item.words)">
                {{ item.words.join(' / ') }}
              </template>
              <template v-else>
                {{ item.word || item.question || item.name || JSON.stringify(item) }}
              </template>
            </div>
          </div>
        </div>
        <div v-if="result.row_count !== undefined" class="result-item">
          <span class="result-label">数据行数：</span>
          <span class="result-value">{{ result.row_count }}</span>
        </div>
        <div v-if="result.field_count !== undefined" class="result-item">
          <span class="result-label">字段数量：</span>
          <span class="result-value">{{ result.field_count }}</span>
        </div>
        <div v-if="result.sql_preview" class="result-item">
          <span class="result-label">SQL预览：</span>
          <pre class="sql-preview">{{ result.sql_preview }}</pre>
        </div>
        <div v-if="result.chart_type" class="result-item">
          <span class="result-label">图表类型：</span>
          <span class="result-value">{{ result.chart_type }}</span>
        </div>
        <div v-if="result.name" class="result-item">
          <span class="result-label">数据源：</span>
          <span class="result-value">{{ result.name }}</span>
        </div>
        <div v-if="result.engine_type" class="result-item">
          <span class="result-label">引擎类型：</span>
          <span class="result-value">{{ result.engine_type }}</span>
        </div>
      </div>
      <div v-if="stepStatus === 'error' && description" class="step-error-message">
        <div class="error-label">错误信息：</div>
        <div class="error-content">{{ description }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Clock, Loading, CircleCheck, CircleClose, ArrowDown, ArrowUp } from '@element-plus/icons-vue'

interface Props {
  step: string
  stepName: string
  description?: string
  status: 'pending' | 'processing' | 'completed' | 'error'
  result?: any
}

const props = withDefaults(defineProps<Props>(), {
  description: '',
  result: undefined,
})

const expanded = ref(false)
const stepStatus = computed(() => props.status)
const hasResult = computed(() => props.result !== undefined && props.result !== null)

const toggleExpand = () => {
  if (hasResult.value && stepStatus.value === 'completed') {
    expanded.value = !expanded.value
  }
}
</script>

<style scoped lang="less">
.process-step-container {
  margin-bottom: 12px;
}

.process-step {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 12px;
  background-color: #f5f5f5;
  transition: all 0.3s ease;

  &.step-pending {
    border-color: #d3d3d3;
    background-color: #f5f5f5;
  }

  &.step-processing {
    border-color: #d3d3d3;
    background-color: #f5f5f5;
    animation: pulse 1.5s ease-in-out infinite;
  }

  &.step-completed {
    border-color: #d3d3d3;
    background-color: #f5f5f5;
  }

  &.step-error {
    border-color: #d3d3d3;
    background-color: #f5f5f5;
  }
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.7;
  }
}

.step-header {
  display: flex;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.step-icon {
  margin-right: 12px;
  font-size: 20px;

  .step-icon-pending {
    color: #909399;
  }

  .step-icon-processing {
    color: #909399;
    animation: rotate 1s linear infinite;
  }

  .step-icon-completed {
    color: #909399;
  }

  .step-icon-error {
    color: #909399;
  }
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.step-info {
  flex: 1;
}

.step-name {
  font-size: 14px;
  font-weight: 500;
  color: #909399;
  margin-bottom: 4px;
}

.step-description {
  font-size: 12px;
  color: #909399;
}

.step-action {
  margin-left: 12px;
}

.expand-icon {
  color: #909399;
  transition: transform 0.3s;
}

.step-result {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #d3d3d3;
}

.result-item {
  margin-bottom: 8px;
  font-size: 13px;

  &:last-child {
    margin-bottom: 0;
  }
}

.result-label {
  color: #909399;
  font-weight: 500;
  margin-right: 8px;
}

.result-value {
  color: #909399;
}

.result-items {
  margin-top: 8px;
}

.items-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.item-tag {
  padding: 4px 8px;
  background-color: #e4e7ed;
  border: 1px solid #d3d3d3;
  border-radius: 4px;
  font-size: 12px;
  color: #909399;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sql-preview {
  margin: 8px 0 0 0;
  padding: 8px;
  background-color: #f5f5f5;
  border: 1px solid #d3d3d3;
  border-radius: 4px;
  font-size: 12px;
  color: #909399;
  overflow-x: auto;
  max-height: 150px;
  overflow-y: auto;
}

.step-error-message {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #e4e7ed;
}

.error-label {
  color: #909399;
  font-weight: 500;
  margin-bottom: 4px;
  font-size: 13px;
}

.error-content {
  color: #909399;
  font-size: 12px;
  padding: 8px;
  background-color: #f5f5f5;
  border: 1px solid #d3d3d3;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>

