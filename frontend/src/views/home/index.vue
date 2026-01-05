<template>
  <div class="home-container">
    <div class="home-content">
      <h1 class="home-title">首页</h1>
      <div class="info-card">
        <h2>接收到的参数：</h2>
        <div class="params">
          <p><strong>Code:</strong> {{ code || '未提供' }}</p>
          <p><strong>State:</strong> {{ state || '未提供' }}</p>
        </div>
      </div>
      <div class="actions">
        <el-button type="primary" @click="goToChat">前往问答</el-button>
        <el-button @click="goToDashboard">前往仪表盘</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useCache } from '@/utils/useCache'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { wsCache } = useCache()

const code = ref<string>('')
const state = ref<string>('')

onMounted(async () => {
  // 检查是否已登录
  const token = wsCache.get('user.token')
  const uid = userStore.getUid
  
  // 如果未登录，检查 OAuth2 并跳转
  if (!token || !uid) {
    try {
      const { getOAuth2Config, redirectToOAuth2Login } = await import('@/utils/oauth2')
      const oauth2Config = await getOAuth2Config()
      
      if (oauth2Config?.enabled) {
        // OAuth2 已开启，跳转到 OAuth2 认证地址
        console.log('User not logged in, OAuth2 is enabled, redirecting to OAuth2 login')
        await redirectToOAuth2Login()
        return
      } else {
        // OAuth2 未开启，跳转到登录页
        console.log('User not logged in, OAuth2 is not enabled, redirecting to login page')
        router.push('/login')
        return
      }
    } catch (error) {
      console.error('Error checking OAuth2 config:', error)
      // 出错时默认跳转到登录页
      router.push('/login')
      return
    }
  }
  
  // 已登录，继续显示首页内容
  // 从路由查询参数中获取 code 和 state
  code.value = (route.query.code as string) || ''
  state.value = (route.query.state as string) || ''
  
  // 输出参数到控制台
  console.log('接收到的参数:')
  console.log('Code:', code.value)
  console.log('State:', state.value)
})

const goToChat = () => {
  router.push('/chat/index')
}

const goToDashboard = () => {
  router.push('/dashboard/index')
}
</script>

<style scoped lang="less">
.home-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.home-content {
  background: white;
  border-radius: 12px;
  padding: 40px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.1);
  max-width: 600px;
  width: 100%;
}

.home-title {
  font-size: 32px;
  font-weight: bold;
  text-align: center;
  color: #333;
  margin-bottom: 30px;
}

.info-card {
  background: #f5f7fa;
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 30px;
}

.info-card h2 {
  font-size: 18px;
  color: #666;
  margin-bottom: 15px;
}

.params {
  font-size: 14px;
  color: #333;
}

.params p {
  margin: 10px 0;
  padding: 8px;
  background: white;
  border-radius: 4px;
  border-left: 3px solid #667eea;
}

.params strong {
  color: #667eea;
  margin-right: 10px;
}

.actions {
  display: flex;
  gap: 15px;
  justify-content: center;
}
</style>

