<script setup lang="ts">
import { ref } from 'vue'
import { chatApi } from '@/api/chat.ts'
import { CircleCheck, CircleClose } from '@element-plus/icons-vue'

const props = withDefaults(
  defineProps<{
    recordId?: number
    disabled?: boolean
  }>(),
  { recordId: undefined, disabled: false }
)

const submitted = ref(false)
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
  <Transition name="feedback-enter">
    <div v-if="recordId" class="feedback-block">
      <template v-if="!submitted">
        <span class="feedback-label">结果有帮助吗？</span>
        <el-button
          class="feedback-btn feedback-btn--like"
          type="primary"
          link
          :disabled="disabled"
          :loading="loading"
          @click="handleLike"
        >
          <el-icon><CircleCheck /></el-icon>
          点赞
        </el-button>
        <el-dropdown trigger="click" :disabled="disabled">
          <el-button
            class="feedback-btn feedback-btn--dislike"
            type="primary"
            link
            :disabled="disabled"
          >
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
      </template>
      <Transition name="done-pop">
        <span v-if="submitted" class="feedback-done">已反馈</span>
      </Transition>
    </div>
  </Transition>
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

  .feedback-btn {
    transition: transform 0.2s ease;

    &:active {
      transform: scale(0.9);
    }
  }

  .feedback-btn--like {
    &:active {
      color: var(--el-color-success);
    }
  }

  .feedback-done {
    color: var(--el-color-success);
    font-size: 13px;
  }
}

.feedback-enter-enter-active {
  transition: opacity 0.3s ease, transform 0.3s ease;
}

.feedback-enter-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.done-pop-enter-active {
  transition: all 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.done-pop-enter-from {
  opacity: 0;
  transform: scale(0.7);
}
</style>
