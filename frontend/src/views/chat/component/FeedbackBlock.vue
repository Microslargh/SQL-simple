<script setup lang="ts">
import { ref } from 'vue'
import { chatApi } from '@/api/chat.ts'
import { ElMessage } from 'element-plus'
import { CircleCheck, CircleClose } from '@element-plus/icons-vue'

const props = withDefaults(
  defineProps<{
    recordId?: number
    disabled?: boolean
  }>(),
  { recordId: undefined, disabled: false }
)

const submitted = ref(false)
const showReasons = ref(false)
const loading = ref(false)

const reasons = [
  { value: 'no_result', label: '查询无结果' },
  { value: 'inaccurate_data', label: '查询到的数据不准确' },
  { value: 'wrong_analysis', label: '分析过程有误' },
]

async function handleLike() {
  if (!props.recordId || props.disabled || submitted.value) return
  loading.value = true
  try {
    await chatApi.feedback(props.recordId, true)
    submitted.value = true
    ElMessage.success('感谢反馈')
  } catch (e: any) {
    ElMessage.error(e?.message || '提交失败')
  } finally {
    loading.value = false
  }
}

async function handleDislikeReason(reason: string) {
  if (!props.recordId || props.disabled || submitted.value) return
  showReasons.value = false
  loading.value = true
  try {
    await chatApi.feedback(props.recordId, false, reason)
    submitted.value = true
    ElMessage.success('反馈已记录，我们会尽快处理')
  } catch (e: any) {
    ElMessage.error(e?.message || '提交失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div v-if="recordId" class="feedback-block">
    <span class="feedback-label">结果有帮助吗？</span>
    <el-button
      type="primary"
      link
      :disabled="disabled || submitted"
      :loading="loading"
      @click="handleLike"
    >
      <el-icon><CircleCheck /></el-icon>
      点赞
    </el-button>
    <el-dropdown
      trigger="click"
      :disabled="disabled || submitted"
      @visible-change="(v: boolean) => (showReasons = v)"
    >
      <el-button type="primary" link :disabled="disabled || submitted">
        <el-icon><CircleClose /></el-icon>
        点踩
      </el-button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item
            v-for="r in reasons"
            :key="r.value"
            @click="handleDislikeReason(r.value)"
          >
            {{ r.label }}
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
    <span v-if="submitted" class="feedback-done">已反馈</span>
  </div>
</template>

<style scoped lang="less">
.feedback-block {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 13px;
  color: #909399;

  .feedback-label {
    margin-right: 4px;
  }

  .feedback-done {
    color: var(--el-color-success);
    margin-left: 4px;
  }
}
</style>
