<template>
  <div class="oauth2-callback-container">
    <div class="loading-content">
      <el-icon class="loading-icon"><Loading /></el-icon>
      <p>{{ loadingText }}</p>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const loadingText = ref('正在处理登录...')

onMounted(async () => {
  const token = route.query.token as string
  
  console.log('OAuth2 callback - token received:', token ? 'Yes' : 'No')
  
  if (!token) {
    ElMessage.error('未获取到登录令牌')
    loadingText.value = '登录失败，正在跳转到登录页...'
    setTimeout(() => {
      router.push('/login')
    }, 2000)
    return
  }
  
  try {
    console.log('Setting token and fetching user info...')
    // 保存token
    userStore.setToken(token)
    
    // 获取用户信息
    console.log('Calling userStore.info()...')
    await userStore.info()
    console.log('User info fetched successfully:', {
      uid: userStore.getUid,
      account: userStore.getAccount,
      name: userStore.getName
    })
    
    ElMessage.success('登录成功')
    loadingText.value = '登录成功，正在跳转...'
    
    // 跳转到首页或聊天页面
    setTimeout(() => {
      console.log('Redirecting to /chat')
      router.push('/chat')
    }, 1000)
  } catch (error: any) {
    console.error('OAuth2 callback error:', error)
    console.error('Error details:', {
      message: error?.message,
      response: error?.response,
      status: error?.response?.status,
      data: error?.response?.data
    })
    ElMessage.error('登录失败: ' + (error?.message || error?.response?.data?.msg || '未知错误'))
    loadingText.value = '登录失败，正在跳转到登录页...'
    setTimeout(() => {
      router.push('/login')
    }, 2000)
  }
})
</script>

<style scoped>
.oauth2-callback-container {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background-color: #f0f2f5;
}

.loading-content {
  text-align: center;
}

.loading-icon {
  font-size: 48px;
  color: #409eff;
  animation: rotate 1s linear infinite;
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.loading-content p {
  margin-top: 20px;
  font-size: 16px;
  color: #666;
}
</style>

