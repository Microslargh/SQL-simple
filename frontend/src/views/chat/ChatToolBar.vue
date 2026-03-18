<script setup lang="ts">
import { datetimeFormat } from '@/utils/utils.ts'
import type { ChatMessage } from '@/api/chat.ts'
import { ElMessage } from 'element-plus-secondary'

const props = defineProps<{
  message: ChatMessage
}>()

function fallbackCopyText(text: string) {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', 'readonly')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  textarea.style.pointerEvents = 'none'
  document.body.appendChild(textarea)
  textarea.focus()
  textarea.select()
  const success = document.execCommand('copy')
  document.body.removeChild(textarea)
  return success
}

async function copyRecordId() {
  const recordId = props.message?.record?.id
  if (!recordId) {
    ElMessage.warning('当前消息还没有 record_id')
    return
  }
  const text = String(recordId)
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const success = fallbackCopyText(text)
      if (!success) {
        throw new Error('fallback copy failed')
      }
    }
    ElMessage.success(`已复制 record_id：${recordId}`)
  } catch {
    try {
      const success = fallbackCopyText(text)
      if (success) {
        ElMessage.success(`已复制 record_id：${recordId}`)
        return
      }
    } catch {
      // ignore
    }
    ElMessage.error('复制失败，请检查浏览器权限')
  }
}
</script>

<template>
  <div class="tool-container">
    <div class="tool-btns">
      <slot></slot>
    </div>
    <div class="tool-times">
      <div
        v-if="message?.record?.id"
        class="record-id"
        role="button"
        tabindex="0"
        title="点击复制记录ID"
        @click="copyRecordId"
      >
        <span class="record-id__label">RID {{ message.record.id }}</span>
      </div>
      <div class="time">
        {{ datetimeFormat(message?.record?.create_time) }}
      </div>
    </div>
  </div>
</template>

<style scoped lang="less">
.tool-container {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;

  row-gap: 8px;

  min-height: 22px;

  margin-top: 12px;
  margin-bottom: 12px;

  .tool-times {
    flex: 1;
    font-size: 14px;
    font-weight: 400;
    line-height: 22px;
    color: rgba(100, 106, 115, 1);

    display: flex;
    flex-direction: row;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;

    .record-id {
      display: inline-flex;
      align-items: center;
      gap: 2px;
      color: rgba(100, 106, 115, 1);
      cursor: pointer;
      user-select: none;
      transition: color 0.2s ease;

      &:hover {
        color: rgba(64, 158, 255, 1);
      }
    }

    .record-id__label {
      font-size: 12px;
      font-weight: 500;
      white-space: nowrap;
    }

    .time {
      white-space: nowrap;
    }
  }
}
</style>
