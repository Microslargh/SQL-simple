<script setup lang="ts">
import { Search } from '@element-plus/icons-vue'
import ChatList from '@/views/chat/ChatList.vue'
import { useI18n } from 'vue-i18n'
import { computed, nextTick, ref } from 'vue'
import { Chat, chatApi, ChatInfo } from '@/api/chat.ts'
import { filter, includes } from 'lodash-es'
import ChatCreator from '@/views/chat/ChatCreator.vue'
import { useAssistantStore } from '@/stores/assistant'
import icon_sidebar_outlined from '@/assets/svg/icon_sidebar_outlined.svg'
import icon_new_chat_outlined from '@/assets/svg/icon_new_chat_outlined.svg'
import { datasourceApi } from '@/api/datasource.ts'
import dayjs from 'dayjs'
const props = withDefaults(
  defineProps<{
    inPopover?: boolean
    chatList?: Array<ChatInfo>
    currentChatId?: number
    currentChat?: ChatInfo
    loading?: boolean
    appName?: string
  }>(),
  {
    chatList: () => [],
    currentChatId: undefined,
    currentChat: () => new ChatInfo(),
    loading: false,
    inPopover: false,
    appName: '',
  }
)

const emits = defineEmits([
  'goEmpty',
  'onChatCreated',
  'onClickHistory',
  'onChatDeleted',
  'onChatRenamed',
  'onClickSideBarBtn',
  'update:loading',
  'update:chatList',
  'update:currentChat',
  'update:currentChatId',
])

const assistantStore = useAssistantStore()
const isCompletePage = computed(() => !assistantStore.getAssistant || assistantStore.getEmbedded)

const search = ref<string>()

// 批量管理模式
const batchMode = ref(false)
const selectedChatIds = ref<Set<number>>(new Set())
const selectedCount = computed(() => selectedChatIds.value.size)

function toggleBatchMode() {
  batchMode.value = !batchMode.value
  if (!batchMode.value) {
    selectedChatIds.value = new Set()
  }
}

function onSelectionChanged(ids: Set<number>) {
  selectedChatIds.value = ids
}

function selectByPeriod(hours: number | null) {
  const now = dayjs()
  const newSelected = new Set<number>()

  for (const chat of _chatList.value) {
    if (!chat.create_time) continue
    const chatTime = dayjs(chat.create_time)

    if (hours === null) {
      // 更早以前：1周前
      const weekAgo = now.subtract(168, 'hour')
      if (chatTime.isBefore(weekAgo)) {
        newSelected.add(chat.id!)
      }
    } else if (hours === -1) {
      // 全部选中
      newSelected.add(chat.id!)
    } else {
      const cutoff = now.subtract(hours, 'hour')
      if (chatTime.isAfter(cutoff)) {
        newSelected.add(chat.id!)
      }
    }
  }

  selectedChatIds.value = newSelected
}

async function onBatchDelete() {
  if (selectedChatIds.value.size === 0) return

  await ElMessageBox.confirm(
    t('qa.batch_delete_confirm', { count: selectedChatIds.value.size }),
    {
      confirmButtonType: 'danger',
      tip: t('common.proceed_with_caution'),
      confirmButtonText: t('dashboard.delete'),
      cancelButtonText: t('common.cancel'),
      customClass: 'confirm-no_icon',
      autofocus: false,
    }
  )

  _loading.value = true
  try {
    const ids = Array.from(selectedChatIds.value)
    await chatApi.batchDeleteChat(ids)
    ElMessage({
      type: 'success',
      message: t('dashboard.delete_success'),
    })
    // 从列表中移除已删除项
    _chatList.value = _chatList.value.filter(c => !ids.includes(c.id!))
    // 如果当前对话被删，回到空状态
    if (_currentChatId.value && ids.includes(_currentChatId.value)) {
      goEmpty()
    }
    batchMode.value = false
    selectedChatIds.value = new Set()
    emits('onChatDeleted', ids)
  } catch (err: any) {
    ElMessage({
      type: 'error',
      message: err.message,
    })
  } finally {
    _loading.value = false
  }
}

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

const computedChatList = computed<Array<ChatInfo>>(() => {
  if (search.value && search.value.length > 0) {
    return filter(_chatList.value, (c) =>
      includes(c.brief?.toLowerCase(), search.value?.toLowerCase())
    )
  } else {
    return _chatList.value
  }
})

const _loading = computed({
  get() {
    return props.loading
  },
  set(v) {
    emits('update:loading', v)
  },
})

const { t } = useI18n()

function onClickSideBarBtn() {
  emits('onClickSideBarBtn')
}

function onChatCreated(chat: ChatInfo) {
  _chatList.value.unshift(chat)
  _currentChatId.value = chat.id
  _currentChat.value = chat
  emits('onChatCreated', chat)
}

const chatCreatorRef = ref()

function goEmpty(func?: (...p: any[]) => void, ...params: any[]) {
  _currentChat.value = new ChatInfo()
  _currentChatId.value = undefined
  emits('goEmpty', func, ...params)
}

const createNewChat = async () => {
  try {
    await chatApi.checkLLMModel()
  } catch (error: any) {
    console.error(error)
    //this.$message.error(error)
    /*

    ElMessageBox.confirm(t('qa.ask_failed'), {


      showCancelButton: userStore.isAdmin,
      confirmButtonText: confirm_text,
      cancelButtonText: t('common.cancel'),
      customClass: 'confirm-no_icon',
      autofocus: false,
      showClose: false,
      callback: (val: string) => {
        if (userStore.isAdmin && val === 'confirm') {
          router.push('/system/model')
        }
      },
    })*/
    return
  }
  goEmpty(doCreateNewChat)
}

async function doCreateNewChat() {
  if (!isCompletePage.value) {
    return
  }
  // 自动选择第一个数据源并创建对话
  try {
    const datasourceList = await datasourceApi.list()
    if (datasourceList && datasourceList.length > 0) {
      const firstDs = datasourceList[0]
      // 检查数据源是否可用
      const isAvailable = await datasourceApi.check_by_id(firstDs.id)
      if (isAvailable) {
        // 数据源可用，直接创建对话
        chatCreatorRef.value?.createChat(firstDs.id)
      } else {
        // 数据源不可用，显示选择对话框
        chatCreatorRef.value?.showDs()
      }
    } else {
      // 没有数据源，显示选择对话框
      chatCreatorRef.value?.showDs()
    }
  } catch (error) {
    console.error('Failed to auto-create chat with first datasource:', error)
    // 出错时显示选择对话框
    chatCreatorRef.value?.showDs()
  }
}

function onClickHistory(chat: Chat) {
  if (chat !== undefined && chat.id !== undefined) {
    if (_currentChatId.value === chat.id) {
      return
    }
    goEmpty(goHistory, chat)
  }
}

function goHistory(chat: Chat) {
  nextTick(() => {
    if (chat !== undefined && chat.id !== undefined) {
      _currentChat.value = new ChatInfo(chat)
      _currentChatId.value = chat.id
      _loading.value = true
      chatApi
        .get(chat.id)
        .then((res) => {
          const info = chatApi.toChatInfo(res)
          if (info && info.id === _currentChatId.value) {
            _currentChat.value = info

            // scrollToBottom()
            emits('onClickHistory', info)
          }
        })
        .finally(() => {
          _loading.value = false
        })
    }
  })
}

function onChatDeleted(id: number) {
  for (let i = 0; i < _chatList.value.length; i++) {
    if (_chatList.value[i].id === id) {
      _chatList.value.splice(i, 1)
      break
    }
  }
  if (id === _currentChatId.value) {
    goEmpty()
  }
  emits('onChatDeleted', id)
}

function onChatRenamed(chat: Chat) {
  _chatList.value.forEach((c: Chat) => {
    if (c.id === chat.id) {
      c.brief = chat.brief
    }
  })
  if (_currentChat.value.id === chat.id) {
    _currentChat.value.brief = chat.brief
  }
  emits('onChatRenamed', chat)
}
</script>

<template>
  <el-container class="chat-container-right-container">
    <el-header class="chat-list-header" :class="{ 'in-popover': inPopover }">
      <div v-if="!inPopover" class="title">
        <div>{{ appName || t('qa.title') }}</div>
        <el-button link type="primary" class="icon-btn" @click="onClickSideBarBtn">
          <el-icon>
            <icon_sidebar_outlined />
          </el-icon>
        </el-button>
      </div>
      <el-button class="btn" type="primary" @click="createNewChat">
        <el-icon style="margin-right: 6px">
          <icon_new_chat_outlined />
        </el-icon>
        {{ t('qa.new_chat') }}
      </el-button>
      <el-input
        v-model="search"
        :prefix-icon="Search"
        class="search"
        name="quick-search"
        autocomplete="off"
        :placeholder="t('qa.chat_search')"
        clearable
      />
      <div class="batch-toggle-row">
        <el-button v-if="!batchMode" text size="small" @click="toggleBatchMode">
          {{ t('qa.manage') }}
        </el-button>
        <el-button v-else text size="small" type="primary" @click="toggleBatchMode">
          {{ t('qa.exit_manage') }}
        </el-button>
      </div>
    </el-header>
    <div v-if="batchMode" class="batch-bar">
      <el-button size="small" @click="selectByPeriod(1)">{{ t('qa.last_hour') }}</el-button>
      <el-button size="small" @click="selectByPeriod(24)">{{ t('qa.last_day') }}</el-button>
      <el-button size="small" @click="selectByPeriod(168)">{{ t('qa.last_week') }}</el-button>
      <el-button size="small" @click="selectByPeriod(null)">{{ t('qa.earlier') }}</el-button>
      <el-button size="small" @click="selectByPeriod(-1)">{{ t('qa.select_all') }}</el-button>
    </div>
    <el-main class="chat-list">
      <div v-if="!computedChatList.length" class="empty-search">
        {{ !!search ? $t('datasource.relevant_content_found') : $t('dashboard.no_chat') }}
      </div>
      <ChatList
        v-else
        v-model:loading="_loading"
        :current-chat-id="_currentChatId"
        :chat-list="computedChatList"
        :batch-mode="batchMode"
        :selected-ids="selectedChatIds"
        @chat-selected="onClickHistory"
        @chat-deleted="onChatDeleted"
        @chat-renamed="onChatRenamed"
        @selection-changed="onSelectionChanged"
      />
    </el-main>
    <div v-if="batchMode && selectedCount > 0" class="batch-footer">
      <span class="batch-footer-text">{{ t('qa.selected_count', { count: selectedCount }) }}</span>
      <el-button type="danger" size="small" @click="onBatchDelete">
        {{ t('qa.batch_delete') }}
      </el-button>
    </div>

    <ChatCreator v-if="isCompletePage" ref="chatCreatorRef" @on-chat-created="onChatCreated" />
  </el-container>
</template>

<style scoped lang="less">
.chat-container-right-container {
  background: rgba(245, 246, 247, 1);

  height: 100%;

  .icon-btn {
    min-width: unset;
    width: 26px;
    height: 26px;
    font-size: 18px;

    &:hover {
      background: rgba(31, 35, 41, 0.1);
    }
  }

  .chat-list-header {
    --ed-header-padding: 16px;
    --ed-header-height: calc(16px + 24px + 16px + 40px + 16px + 32px + 16px);

    &.in-popover {
      --ed-header-height: calc(16px + 40px + 16px + 32px + 16px);
    }

    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    gap: 16px;

    .title {
      height: 24px;
      width: 100%;
      display: flex;
      flex-direction: row;
      align-items: center;
      justify-content: space-between;
      font-weight: 500;
    }

    .btn {
      width: 100%;
      height: 40px;

      font-size: 16px;
      font-weight: 500;

      --ed-button-text-color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      --ed-button-bg-color: var(--ed-color-primary-1a, #1cba901a);
      --ed-button-border-color: var(--ed-color-primary-60, #a4e3d3);
      --ed-button-hover-bg-color: var(--ed-color-primary-80, #d2f1e9);
      --ed-button-hover-text-color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      --ed-button-hover-border-color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      --ed-button-active-bg-color: var(--ed-color-primary-60, #a4e3d3);
      --ed-button-active-border-color: var(--ed-color-primary, rgba(28, 186, 144, 1));
    }

    .search {
      height: 32px;
      width: 100%;
      :deep(.ed-input__wrapper) {
        background-color: #f5f6f7;
      }
    }
  }

  .chat-list {
    padding: 0 0 20px 0;

    .empty-search {
      width: 100%;
      text-align: center;
      margin-top: 80px;
      color: #646a73;
      font-weight: 400;
      font-size: 14px;
      line-height: 22px;
    }
  }

  .batch-toggle-row {
    width: 100%;
    display: flex;
    justify-content: flex-end;
    margin-top: -8px;
  }

  .batch-bar {
    padding: 0 16px 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .batch-footer {
    position: sticky;
    bottom: 0;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: rgba(245, 246, 247, 0.95);
    border-top: 1px solid #dee0e3;
    z-index: 10;

    &-text {
      font-size: 13px;
      color: #646a73;
    }
  }
}
</style>
