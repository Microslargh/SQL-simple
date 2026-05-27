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

const autoLogin = (): Promise<void> => {
  return new Promise((resolve) => {
    try {
      // 尝试使用钉钉 JSAPI 获取免登授权码
      const dd = (window as any).dd
      if (!dd || !dd.ready) {
        console.log('DingTalk JSAPI not available, skipping auto login')
        resolve()
        return
      }
      console.log('env:', dd.env?.platform)
      dd.ready(() => {
        const codeID = import.meta.env.MODE === 'development'
          ? 'dingc9d2820241461f3df2c783f7214b6d69'
          : 'dingfb48100d0e532caa24f2f5cc6abecb85'
        dd.runtime.permission
          .requestAuthCode({ corpId: codeID })
          .then((onSuccess: any) => {
            console.log('DingTalk auth code success:', onSuccess)
            return fetchData(onSuccess.code)
          })
          .then(() => resolve())
          .catch((err: any) => {
            console.error('DingTalk requestAuthCode failed:', err)
            resolve()
          })
      })
      dd.error?.(() => {
        console.error('DingTalk JSAPI error')
        resolve()
      })
    } catch (e) {
      console.error('autoLogin error:', e)
      resolve()
    }
  })
}

const fetchData = (code: string): Promise<void> => {
  return uer_info_Api.uerInfo(code)
    .then((res: any) => {
      console.log(res, '返回的用户数据')
      const { userInfo: { unionid, name, jobnumber }, accessToken } = res
      wsCache.set('user.token', accessToken)
      console.log(wsCache.get('user.token'), 'accessToken')
      console.log(name, 'name')
      console.log(unionid, 'unionid')
      console.log(jobnumber, 'jobnumber')
      userStore.setUerName(name)
      userStore.setJobnumber(jobnumber)
    })
    .catch((err: any) => {
      console.error('fetchData failed:', err)
    })
}

const fetchJobNumber = (code: string): Promise<void> => {
  return uer_info_Api.jobNumber(code)
    .then((res: any) => {
      console.log(res, '返回的用户数据')
      const { userInfo: { unionid, name, jobnumber }, accessToken } = res
      wsCache.set('user.token', accessToken)
      console.log(wsCache.get('user.token'), accessToken)
      console.log(unionid, 'unionid')
      userStore.setUerName(name)
      userStore.setJobnumber(jobnumber)
    })
    .catch((err: any) => {
      console.error('fetchJobNumber failed:', err)
    })
}
// 标记是否正在执行无感登录（避免重复请求）
export const watchRouter = (router: Router) => {

  router.beforeEach(async (to: any, from: any, next: any) => {
    try {
      if (import.meta.env.MODE === 'development') {
        await fetchJobNumber('P638418')
      } else {
        await autoLogin()
      }
      await loadXpackStatic()
      await appearanceStore.setAppearance()
      if (typeof LicenseGenerator !== 'undefined') {
        LicenseGenerator.generateRouters(router)
      }
    } catch (e) {
      console.error('Router guard init error:', e)
    }

    const token = wsCache.get('user.token')
    console.log(token, 'token333')
    console.log(to.path, from.path, 'beforeEach')

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
      await userStore.info()
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
  return new Promise((resolve) => {
    request
      .loadRemoteScript(url, 'sqlbot_xpack_static', () => {
        const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
        if (typeof LicenseGenerator !== 'undefined') {
          LicenseGenerator?.init(baseUrl).then(() => resolve(true)).catch(() => resolve(true))
        } else {
          resolve(true)
        }
      })
      .catch((error) => {
        console.error('Failed to load xpack_static script:', error)
        resolve(true)
      })
  })
}


