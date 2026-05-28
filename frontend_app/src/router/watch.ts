import { ElMessage } from 'element-plus-secondary'
import { useCache } from '@/utils/useCache'
import { useAppearanceStoreWithOut } from '@/stores/appearance'
import { useUserStore } from '@/stores/user'
import { request } from '@/utils/request'
import type { Router } from 'vue-router'
import { uer_info_Api } from '@/api/prompt'

const appearanceStore = useAppearanceStoreWithOut()
const userStore = useUserStore()
const { wsCache } = useCache()

const whiteList = ['/login']
const assistantWhiteList = ['/assistant', '/embeddedPage', '/401', '/chat']

import * as dd from 'dingtalk-jsapi'

const autoLogin = () => {
  console.log('env:', dd.env.platform)
  dd.ready(() => {
    dd.runtime.permission
      .requestAuthCode({
        corpId: 'dingfb48100d0e532caa24f2f5cc6abecb85',
      })
      .then((onSuccess: any) => {
        console.log('success: ', onSuccess)
        fetchData(onSuccess.code)
      })
  })
}

const fetchData = (code: string) => {
  uer_info_Api
    .uerInfo(code)
    .then((res: any) => {
      console.log(res, '返回的用户数据')
      const {
        userInfo: { unionid, name, jobnumber },
        accessToken,
      } = res
      wsCache.set('user.token', accessToken)
      console.log(wsCache.get('user.token'), 'accessToken')
      console.log(name, 'name')
      console.log(unionid, 'unionid')
      console.log(jobnumber, 'jobnumber')
      userStore.setUerName(name)
      userStore.setJobnumber(jobnumber)
    })
    .finally(() => {})
}

export const watchRouter = (router: Router) => {
  router.beforeEach(async (to: any, from: any, next: any) => {
    autoLogin()

    try {
      await loadXpackStatic()
    } catch (e) {
      console.error('loadXpackStatic failed:', e)
    }

    try {
      await appearanceStore.setAppearance()
    } catch (e) {
      console.error('setAppearance failed:', e)
    }

    try {
      if (typeof LicenseGenerator !== 'undefined') {
        LicenseGenerator.generateRouters(router)
      }
    } catch (e) {
      console.error('generateRouters failed:', e)
    }

    const token = wsCache.get('user.token')

    if (to.path.startsWith('/login') && userStore.getUid) {
      next('/')
      return
    }
    if (assistantWhiteList.includes(to.path)) {
      next()
      return
    }
    if (whiteList.includes(to.path)) {
      next()
      return
    }
    if (!token) {
      next('/login')
      return
    }
    if (!userStore.getUid) {
      try {
        await userStore.info()
      } catch (e) {
        console.error('userStore.info failed:', e)
      }
    }
    if (to.path === '/' || accessCrossPermission(to)) {
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
        LicenseGenerator?.init(import.meta.env.VITE_API_BASE_URL).then(() => {
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


