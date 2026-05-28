<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount,onMounted, ref } from 'vue'
import { endsWith, startsWith } from 'lodash-es'
import { useI18n } from 'vue-i18n'
import { chatApi, ChatInfo } from '@/api/chat.ts'
import { hot_Question_Api } from '@/api/prompt'
const props = withDefaults(
  defineProps<{
    recordId?: number
    currentChat?: ChatInfo
    questions?: string
    firstChat?: boolean
    disabled?: boolean
  }>(),
  {
    recordId: undefined,
    currentChat: () => new ChatInfo(),
    questions: '[]',
    firstChat: false,
    disabled: false,
  }
)

const emits = defineEmits(['clickQuestion', 'update:currentChat', 'stop', 'loadingOver'])

const loading = ref(false)

const _currentChat = computed({
  get() {
    return props.currentChat
  },
  set(v) {
    emits('update:currentChat', v)
  },
})

const computedQuestions = computed<string>(() => {
  if (
    props.questions &&
    props.questions.length > 0 &&
    startsWith(props.questions.trim(), '[') &&
    endsWith(props.questions.trim(), ']')
  ) {
    return JSON.parse(props.questions)
  }
  return []
})

const { t } = useI18n()

function clickQuestion(question: string): void {
  if (!props.disabled) {
    emits('clickQuestion', question)
  }
}

const stopFlag = ref(false)

async function getRecommendQuestions() {
  stopFlag.value = false
  loading.value = true
  try {
    const controller: AbortController = new AbortController()
    const response = await chatApi.recommendQuestions(props.recordId, controller)
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')

    let tempResult = ''

    while (true) {
      if (stopFlag.value) {
        controller.abort()
        loading.value = false
        break
      }

      const { done, value } = await reader.read()
      if (done) {
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
              return
            }

            switch (data.type) {
              case 'recommended_question':
                if (
                  data.content &&
                  data.content.length > 0 &&
                  startsWith(data.content.trim(), '[') &&
                  endsWith(data.content.trim(), ']')
                ) {
                  if (_currentChat.value?.records) {
                    for (let record of _currentChat.value.records) {
                      if (record.id === props.recordId) {
                        record.recommended_question = data.content

                        await nextTick()
                      }
                    }
                  }
                }
            }
          }
        }
      }
    }
  } finally {
    loading.value = false
    emits('loadingOver')
  }
}
const hotQuestionList = ref<any[]>([])

const hotQuestion = () => {
    hot_Question_Api.getMatch()
    .then((res: any) => {
     hotQuestionList.value = res.hotQuestions || []
      console.log('[  hotQuestionList] >', hotQuestionList.value)
      // toggleRowLoading.value = true
      // fieldList.value = res.records
      // pageInfo.total = res.total
      // searchLoading.value = false
      
    })
    .finally(() => {
      // searchLoading.value = false
    })
}
onMounted(()=>{
  hotQuestion()
})
function stop() {
  stopFlag.value = true
  loading.value = false
  emits('stop')
}

onBeforeUnmount(() => {
  stop()
})

defineExpose({ getRecommendQuestions, id: () => props.recordId, stop })
</script>
 <template>
  <div v-if="computedQuestions.length > 0 || loading || (firstChat && hotQuestionList.length > 0)" class="recommend-questions">

    <div v-if="firstChat" style="margin-bottom: 8px;text-align:center;">热门问题:</div>
    <div v-else class="continue-ask">{{ t('qa.continue_to_ask') }}</div>
    <div v-if="loading">
      <el-button style="min-width: unset" type="primary" link loading />
    </div>
    <div  class="question-grid">
      <template v-if="firstChat">
        <div
          v-for="(question, index) in hotQuestionList"
          :key="index"
          class="question"
          :class="{ disabled: disabled }"
          @click="clickQuestion(question)"
        >
          {{ question }}
        </div>
      </template>
      <template v-if="!firstChat">
        <div
          v-for="(question, index) in computedQuestions"
          :key="index"
          class="question"
          :class="{ disabled: disabled }"
          @click="clickQuestion(question)"
        >
          {{ question }}
        </div>
      </template>
    </div>
  </div>
</template>
<!--<template>
  <div v-if="computedQuestions.length > 0 || loading" class="recommend-questions">
    <div v-if="firstChat" style="margin-bottom: 8px;text-align: center;">{{ t('qa.guess_u_ask') }}</div>
    <div v-else class="continue-ask">{{ t('qa.continue_to_ask') }}</div>
    <div style="text-align: center;" v-if="loading">
      <el-button style="min-width: unset" type="primary" link loading />
    </div>
    <div v-else class="question-grid">
      <div
        v-for="(question, index) in computedQuestions"
        :key="index"
        class="question"
        :class="{ disabled: disabled }"
        @click="clickQuestion(question)"
      >
        {{ question }}
      </div>
    </div>
  </div>
</template> -->

<style scoped lang="less">
.recommend-questions {
  font-size: 14px;
  font-weight: 500;
  line-height: 22px;
  display: flex;
  flex-direction: column;
  gap: 4px;

  .continue-ask {
    color: rgba(100, 106, 115, 1);
    font-weight: 400;
  }

  .question-grid {
    display: grid;
    grid-gap: 6px;
    margin:10px
    // grid-template-columns: repeat(2, calc(50% - 6px));
  }

  .question {
    font-weight: 400;
    cursor: pointer;
    background: rgba(245, 246, 247, 1);
    min-height: 32px;
    border-radius: 6px;
    padding: 5px 12px;
    line-height: 22px;
    &:hover {
      background: rgba(31, 35, 41, 0.1);
    }
    &.disabled {
      cursor: not-allowed;
      background: rgba(245, 246, 247, 1);
    }
  }
}
</style>
