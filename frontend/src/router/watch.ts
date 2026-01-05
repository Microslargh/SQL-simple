import { ElMessage } from 'element-plus-secondary'
import { useCache } from '@/utils/useCache'
import { useAppearanceStoreWithOut } from '@/stores/appearance'
import { useUserStore } from '@/stores/user'
import { request } from '@/utils/request'
import type { Router } from 'vue-router'

const appearanceStore = useAppearanceStoreWithOut()
const userStore = useUserStore()
const { wsCache } = useCache()
const whiteList = ['/login', '/oauth2/callback', '/403', '/401', '/404']
const assistantWhiteList = ['/assistant', '/embeddedPage']
export const watchRouter = (router: Router) => {
  router.beforeEach(async (to: any, from: any, next: any) => {
    await loadXpackStatic()
    await appearanceStore.setAppearance()
    LicenseGenerator.generateRouters(router)
    if (to.path.startsWith('/login') && userStore.getUid) {
      next('/')
      return
    }
    if (assistantWhiteList.includes(to.path)) {
      next()
      return
    }
    const token = wsCache.get('user.token')
    if (whiteList.includes(to.path)) {
      next()
      return
    }
    if (!token) {
      // 如果是访问首页且未登录，检查 OAuth2 是否开启
      if (to.path === '/') {
        try {
          const { getOAuth2Config, redirectToOAuth2Login } = await import('@/utils/oauth2')
          const oauth2Config = await getOAuth2Config()

          if (oauth2Config?.enabled) {
            // OAuth2 已开启，跳转到 OAuth2 认证地址
            console.log('OAuth2 is enabled, redirecting to OAuth2 login')
            await redirectToOAuth2Login()
            return
          } else {
            // OAuth2 未开启，跳转到登录页
            console.log('OAuth2 is not enabled, redirecting to login page')
            next('/login')
            return
          }
        } catch (error) {
          console.error('Error checking OAuth2 config:', error)
          // 出错时默认跳转到登录页
          next('/login')
          return
        }
      }
      // 其他未登录情况，跳转到登录页（支持多种登录方式：账密登录、OAuth2、钉钉登录）
      next('/login')
      return
    }
    // 确保用户信息已加载（无论是否有 uid，都重新加载一次以确保权限信息是最新的）
    if (!userStore.getUid || userStore.getWeight === undefined || userStore.getWeight === 0) {
      try {
        await userStore.info()
      } catch (error) {
        console.error('Failed to load user info:', error)
        // 如果加载失败，跳转到登录页
        next('/login')
        return
      }
    }
    
    // 调试信息：检查用户权限状态
    if (to.path.startsWith('/system')) {
      console.log('Accessing system route:', to.path)
      console.log('User info:', {
        uid: userStore.getUid,
        weight: userStore.getWeight,
        isAdmin: userStore.isAdmin,
        isSpaceAdmin: userStore.isSpaceAdmin
      })
    }
    
    // 检查权限：如果没有权限访问系统管理，跳转到 403 页面
    if (accessCrossPermission(to)) {
      console.warn('Access denied: User does not have permission to access', to.path, {
        isAdmin: userStore.isAdmin,
        isSpaceAdmin: userStore.isSpaceAdmin,
        weight: userStore.getWeight,
        uid: userStore.getUid
      })
      next('/403')
      return
    }
    
    // 访问首页时跳转到聊天页面
    if (to.path === '/') {
      next('/chat')
      return
    }
    if (to.path === '/login') {
      console.info(from)
      next('/chat')
    } else {
      next()
    }
  })
}

const accessCrossPermission = (to: any) => {
  if (!to?.path) return false
  return (
    (to.path.startsWith('/system') && !userStore.isAdmin) ||
    (to.path.startsWith('/set') && !userStore.isSpaceAdmin)
  )
}
const loadXpackStatic = () => {
  if (document.getElementById('sqlbot_xpack_static')) {
    return Promise.resolve()
  }
  const url = `/xpack_static/license-generator.umd.js?t=${Date.now()}`
  return new Promise((resolve, reject) => {
    request
      .loadRemoteScript(url, 'sqlbot_xpack_static', () => {
        LicenseGenerator?.init(import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1').then(() => {
          resolve(true)
        })
      })
      .catch((error) => {
        console.error('Failed to load xpack_static script:', error)
        ElMessage.error('Failed to load license generator script')
        reject(error)
      })
  })
}
