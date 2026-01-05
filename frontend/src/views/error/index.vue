<template>
  <div class="error-page">
    <div class="error-content">
      <div class="error-icon">
        <Icon v-if="errorType === '403'" name="403"><Forbidden class="svg-icon" /></Icon>
        <Icon v-else-if="errorType === '404'" name="404"><NotFound class="svg-icon" /></Icon>
        <Icon v-else name="401"><Four class="svg-icon" /></Icon>
      </div>
      <div class="error-text">
        <h1 class="error-title">{{ title || getDefaultTitle() }}</h1>
        <p class="error-description">{{ description || getDefaultDescription() }}</p>
        <div class="error-actions">
          <el-button type="primary" @click="goBack">返回上一页</el-button>
          <el-button @click="goHome">返回首页</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import Four from '@/assets/svg/401.svg'
import Forbidden from '@/assets/svg/401.svg'
import NotFound from '@/assets/svg/401.svg'
import { Icon } from '@/components/icon-custom'
import { propTypes } from '@/utils/propTypes'
// import { computed } from 'vue'
import { useRouter } from 'vue-router'

// const route = useRoute()
const router = useRouter()

const props = defineProps({
  title: propTypes.string,
  description: propTypes.string,
  errorType: propTypes.string.def('403'),
})

// const routerTitle = computed(() => route.query?.title || '')

const getDefaultTitle = () => {
  switch (props.errorType) {
    case '403':
      return '访问被拒绝'
    case '404':
      return '页面不存在'
    default:
      return '未授权访问'
  }
}

const getDefaultDescription = () => {
  switch (props.errorType) {
    case '403':
      return '抱歉，您没有权限访问此页面。'
    case '404':
      return '抱歉，您访问的页面不存在。'
    default:
      return '抱歉，您需要登录才能访问此页面。'
  }
}

const goBack = () => {
  router.go(-1)
}

const goHome = async () => {
  // router.push('/chat')
  const { getOAuth2Config, redirectToOAuth2Login } = await import('@/utils/oauth2')
  const oauth2Config = await getOAuth2Config()

  if (oauth2Config?.enabled) {
    // OAuth2已启用，跳转到OAuth2登录认证页面
    await redirectToOAuth2Login()
    return
  } else {
    // OAuth2未配置，跳转到配置的登出地址或默认登录页
    const logoutUrl = import.meta.env.VITE_LOGOUT_URL
    window.location.href = logoutUrl
    return
  }
}
</script>

<style lang="less" scoped>
.error-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background-color: var(--ed-fill-color-light);
  padding: 20px;
}

.error-content {
  display: flex;
  align-items: center;
  gap: 40px;
  max-width: 800px;
  padding: 40px;
  background: var(--ed-bg-color);
  border-radius: 12px;
  box-shadow: var(--ed-box-shadow-light);
}

.error-icon {
  flex-shrink: 0;

  .svg-icon {
    width: 200px;
    height: 200px;
    color: var(--ed-color-primary);
  }
}

.error-text {
  flex: 1;
}

.error-title {
  font-size: 24px;
  font-weight: 600;
  color: var(--ed-text-color-primary);
  margin: 0 0 16px 0;
}

.error-description {
  font-size: 16px;
  color: var(--ed-text-color-secondary);
  margin: 0 0 24px 0;
  line-height: 1.6;
}

.error-actions {
  display: flex;
  gap: 12px;
}

@media (max-width: 768px) {
  .error-content {
    flex-direction: column;
    text-align: center;
    gap: 24px;
  }

  .error-actions {
    justify-content: center;
  }

  .svg-icon {
    width: 120px;
    height: 120px;
  }
}
</style>
