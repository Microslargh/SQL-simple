<template>
  <div class="welcome-container">
    <el-card class="welcome-card">
      <h1 class="welcome-title">Welcome</h1>
      <p class="welcome-message">
        You have successfully logged into the system and can now start using various functions.
      </p>
      <el-button type="primary" class="logout-btn" @click="logout">Logout</el-button>
    </el-card>
  </div>
</template>

<script lang="ts" setup>
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()

const logout = async () => {
  try {
    // 在登出前获取 register_type
    const registerType = userStore.register_type || 0
    
    // 等待登出完成（包括后端接口调用和本地状态清除）
    await userStore.logout()
    
    // 登出成功后，根据 register_type 决定跳转
    if (registerType === 1) {
      // register_type=1：跳转到OAuth2登录认证平台（与访问/时的逻辑一致）
      const { getOAuth2Config, redirectToOAuth2Login } = await import('@/utils/oauth2')
      const oauth2Config = await getOAuth2Config()
      
      if (oauth2Config?.enabled) {
        // OAuth2已启用，跳转到OAuth2登录认证页面
        await redirectToOAuth2Login()
        return
      } else {
        // OAuth2未配置，跳转到登录页
        window.location.href = '/#/login'
        return
      }
    } else {
      // register_type=0：跳转到登录页
      window.location.href = '/#/login'
      return
    }
  } catch (error) {
    console.error('Logout error:', error)
    // 出错时也跳转到登录页（确保用户能够重新登录）
    window.location.href = '/#/login'
  }
}
</script>

<style lang="less" scoped>
.welcome-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  background-color: #f5f7fa;

  .welcome-card {
    width: 500px;
    padding: 30px;
    text-align: center;

    .welcome-title {
      color: #409eff;
      margin-bottom: 20px;
    }

    .welcome-message {
      margin-bottom: 30px;
      font-size: 16px;
    }

    .logout-btn {
      width: 200px;
    }
  }
}
</style>
