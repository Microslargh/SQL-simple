<script setup lang="ts">
import BaseAnswer from './BaseAnswer.vue'
import { Chat, chatApi, ChatInfo, type ChatMessage, ChatRecord, questionApi } from '@/api/chat.ts'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import ChartBlock from '@/views/chat/chat-block/ChartBlock.vue'
import ProcessStep from '@/views/chat/component/ProcessStep.vue'
import MdComponent from '@/views/chat/component/MdComponent.vue'
import {ArrowDown, ArrowUp } from '@element-plus/icons-vue'
interface ProcessStepData {
  step: string
  stepName: string
  description?: string
  status: 'pending' | 'processing' | 'completed' | 'error'
  result?: any
}
const props = withDefaults(
  defineProps<{
    chatList?: Array<ChatInfo>
    currentChatId?: number
    currentChat?: ChatInfo
    message?: ChatMessage
    loading?: boolean
    reasoningName: 'sql_answer' | 'chart_answer' | Array<'sql_answer' | 'chart_answer'>
  }>(),
  {
    chatList: () => [],
    currentChatId: undefined,
    currentChat: () => new ChatInfo(),
    message: undefined,
    loading: false,
  }
)

const emits = defineEmits([
  'finish',
  'error',
  'stop',
  'scrollBottom',
  'update:loading',
  'update:chatList',
  'update:currentChat',
  'update:currentChatId',
])

const index = computed(() => {
  if (props.message?.index) {
    return props.message.index
  }
  if (props.message?.index === 0) {
    return 0
  }
  return -1
})

const _currentChatId = computed({
  get() {
    return props.currentChatId
  },
  set(v) {
    emits('update:currentChatId', v)
  },
})

const _currentChat = computed({
  get() {
    return props.currentChat
  },
  set(v) {
    emits('update:currentChat', v)
  },
})

const _chatList = computed({
  get() {
    return props.chatList
  },
  set(v) {
    emits('update:chatList', v)
  },
})

const _loading = computed({
  get() {
    return props.loading
  },
  set(v) {
    emits('update:loading', v)
  },
})

const stopFlag = ref(false)

// 步骤状态管理
const processSteps = ref<Map<string, ProcessStepData>>(new Map())

// 步骤顺序定义
const stepOrder = [
  'datasource-select',
  'terminology-retrieval',
  'training-retrieval',
  'table-retrieval',
  'sql-generation',
  'sql-execution',
  'chart-generation',
  'result-display',
  'data-analysis',
]

// 获取有序的步骤列表
const orderedSteps = computed(() => {
  return stepOrder
    .map((step) => processSteps.value.get(step))
    .filter((step) => step !== undefined) as ProcessStepData[]
})

// 更新步骤状态
const updateStepStatus = (
  step: string,
  stepName: string,
  status: 'pending' | 'processing' | 'completed' | 'error',
  description?: string,
  result?: any
) => {
  const existingStep = processSteps.value.get(step)
  if (existingStep) {
    existingStep.status = status
    // 如果提供了新的description，则更新；如果状态变为completed但没有description，保持原有description
    if (description !== undefined) {
      existingStep.description = description
    }
    if (result !== undefined) existingStep.result = result
  } else {
    processSteps.value.set(step, {
      step,
      stepName,
      status,
      description: description || '',
      result,
    })
  }
}

const sendMessage = async () => {
  stopFlag.value = false
  _loading.value = true

  if (index.value < 0) {
    _loading.value = false
    return
  }

  const currentRecord: ChatRecord = _currentChat.value.records[index.value]

  let error: boolean = false
  if (_currentChatId.value === undefined) {
    error = true
  }
  if (error) return

  try {
    const controller: AbortController = new AbortController()
    const param = {
      question: currentRecord.question,
      chat_id: _currentChatId.value,
    }
    const response = await questionApi.add(param, controller)
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')

    let sql_answer = ''
    let chart_answer = ''
    let analysis_answer = ''
    let analysis_answer_thinking = ''

    // 重置步骤状态
    processSteps.value.clear()

    let tempResult = ''

    while (true) {
      if (stopFlag.value) {
        controller.abort()
        break
      }

      const { done, value } = await reader.read()
      if (done) {
        _loading.value = false
        break
      }

      let chunk = decoder.decode(value, { stream: true })
      tempResult += chunk
      const split = tempResult.match(/data:.*}\n\n/g)
      if (split) {
        chunk = split.join('')
        tempResult = tempResult.replace(chunk, '')
      } else {
        continue
      }
      if (chunk && chunk.startsWith('data:{')) {
        if (split) {
          for (const str of split) {
            let data
            try {
              data = JSON.parse(str.replace('data:{', '{'))
            } catch (err) {
              console.error('JSON string:', str)
              throw err
            }

            if (data.code && data.code !== 200) {
              ElMessage({
                message: data.msg,
                type: 'error',
                showClose: true,
              })
              _loading.value = false
              return
            }

            switch (data.type) {
              case 'id':
                currentRecord.id = data.id
                _currentChat.value.records[index.value].id = data.id
                break
              case 'info':
                console.info(data.msg)
                break
              case 'brief':
                _currentChat.value.brief = data.brief
                _chatList.value.forEach((c: Chat) => {
                  if (c.id === _currentChat.value.id) {
                    c.brief = _currentChat.value.brief
                  }
                })
                break
              case 'error':
                currentRecord.error = data.content
                emits('error')
                break
              case 'step-start':
                // 步骤开始
                updateStepStatus(data.step, data.step_name, 'processing', data.description)
                break
              case 'step-complete':
                // 步骤完成
                updateStepStatus(data.step, data.step_name, 'completed', data.description, data.result)
                break
              case 'step-error':
                // 步骤错误
                updateStepStatus(data.step, data.step_name, 'error', data.error)
                break
              case 'sql-result':
                sql_answer += data.reasoning_content
                _currentChat.value.records[index.value].sql_answer = sql_answer
                break
              case 'sql':
                _currentChat.value.records[index.value].sql = data.content
                break
              case 'sql-data':
                getChatData(_currentChat.value.records[index.value].id)
                break
              case 'chart-result':
                chart_answer += data.reasoning_content
                _currentChat.value.records[index.value].chart_answer = chart_answer
                break
              case 'chart':
                _currentChat.value.records[index.value].chart = data.content
                // 图表数据已设置，立即触发数据获取以显示图表
                if (_currentChat.value.records[index.value].id) {
                  getChatData(_currentChat.value.records[index.value].id)
                }
                break
              case 'analysis-result':
                analysis_answer += data.content
                analysis_answer_thinking += data.reasoning_content || ''
                _currentChat.value.records[index.value].analysis = analysis_answer
                _currentChat.value.records[index.value].analysis_thinking = analysis_answer_thinking
                break
              case 'analysis_finish':
                // 分析完成，但继续等待图表
                break
              case 'finish':
                emits('finish', currentRecord.id)
                break
            }
            await nextTick()
          }
        }
      }
    }
  } catch (error) {
    if (!currentRecord.error) {
      currentRecord.error = ''
    }
    if (currentRecord.error.trim().length !== 0) {
      currentRecord.error = currentRecord.error + '\n'
    }
    currentRecord.error = currentRecord.error + 'Error:' + error
    console.error('Error:', error)
    emits('error')
  } finally {
    _loading.value = false
  }
}

function getChatData(recordId?: number) {
  chatApi
    .get_chart_data(recordId)
    .then((response) => {
      _currentChat.value.records.forEach((record) => {
        if (record.id === recordId) {
          record.data = response
        }
      })
    })
    .finally(() => {
      emits('scrollBottom')
    })
}
function stop() {
  stopFlag.value = true
  _loading.value = false
  emits('stop')
}

onBeforeUnmount(() => {
  stop()
})

// 定义需要展示的4条文案
const texts = [
  "术语检索",
  "训练数据检索",
  "SQL生成",
  "SQL执行",
  "图表生成",
  "展示结果",
];
// 当前显示的文案索引
const currentIndex = ref(0);
// 当前显示的文案
const currentText = ref(texts[0]);
onMounted(() => {
  if (props.message?.record?.id && props.message?.record?.finish) {
    getChatData(props.message.record.id)
  }

  // 组件挂载后启动定时器，每5秒切换一次文案
  setInterval(() => {
    // 检查是否已经是最后一条文案
    if (currentIndex.value < texts.length - 1) {
      // 切换到下一条文案
      currentIndex.value++;
      currentText.value = texts[currentIndex.value];
    } else {
      // 如果是最后一条文案，清除定时器停止切换
      // clearInterval(intervalId);
      // intervalId = null; // 清空定时器标识
    }
  }, 7000); // 5000毫秒 = 5秒
})

defineExpose({ sendMessage, index: () => index.value, stop })
const ArrowDownT = ref(false)
const ArrowDownF = () => {
  ArrowDownT.value = !ArrowDownT.value
}
</script>

<template>
  <BaseAnswer v-if="message" :message="message" :reasoning-name="reasoningName" :loading="_loading">
    <!-- 步骤展示区域 -->
    <div v-if="orderedSteps.length > 0" class="process-steps-container">
      <div class="process-steps-title">
      <span>
        推理过程
      </span>
        <div @click="ArrowDownF" style="height: 22px;width: 22px;margin-left:20px;">
          <ArrowDown v-if="!ArrowDownT"></ArrowDown>
          <ArrowUp v-else></ArrowUp>
        </div>
      </div>

      <div v-show="ArrowDownT||orderedSteps.length!=7">
        <ProcessStep
          v-for="step in orderedSteps"
          :key="step.step"
          :step="step.step"
          :step-name="step.stepName"
          :description="step.description"
          :status="step.status"
          :result="step.result"
        />
      </div>
    </div>
    <!-- 先显示图表 -->
    <ChartBlock style="margin-top: 6px" :message="message" />
    <!-- 再显示分析结果（如果有） -->
    <div v-if="!message?.record?.analysis" class="text-rotator-container"> 
    <!-- 生产钉钉bug兼容处理 -->
        <!-- 使用transition组件实现淡入动画 -->
        <transition name="fade">
          <span 
            
            class="current-text" 
            :key="currentIndex"
            style="color:#646a73"
          >
            {{ currentText }}
          </span>
        </transition>
        <el-button style="min-width: unset" type="primary" link loading />
          <el-skeleton :rows="3" animated />
      </div>
    <div v-if="message?.record?.analysis" style="margin-top: 12px; margin-bottom: 12px">
      <MdComponent :message="message.record.analysis" />
    </div>
    <slot></slot>
    <template #tool>
      <slot name="tool"></slot>
    </template>
    <template #footer>
      <slot name="footer"></slot>
    </template>
  </BaseAnswer>
</template>

<style scoped lang="less">
.process-steps-container {
  margin-bottom: 16px;
  padding: 12px;
  background-color: #f5f5f5;
  border-radius: 6px;
  border: 1px solid #d3d3d3;
}

.process-steps-title {
  display: flex;
  font-size: 14px;
  font-weight: 500;
  color: #909399;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #d3d3d3;
}
</style>
