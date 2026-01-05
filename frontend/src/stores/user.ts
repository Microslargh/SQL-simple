import { defineStore } from 'pinia'
// import { ref } from 'vue'
import { AuthApi } from '@/api/login'
import { useCache } from '@/utils/useCache'
import { i18n } from '@/i18n'
import { store } from './index'

const { wsCache } = useCache()

interface UserState {
  token: string
  uid: string
  account: string
  name: string
  oid: string
  language: string
  exp: number
  time: number
  weight: number
  register_type: number
  [key: string]: string | number
}

export const UserStore = defineStore('user', {
  state: (): UserState => {
    return {
      token: '',
      uid: '',
      account: '',
      name: '',
      oid: '',
      language: 'zh-CN',
      exp: 0,
      time: 0,
      weight: 0,
      register_type: 0,
    }
  },
  getters: {
    getToken(): string {
      return this.token
    },
    getUid(): string {
      return this.uid
    },
    getAccount(): string {
      return this.account
    },
    getName(): string {
      return this.name
    },
    getOid(): string {
      return this.oid
    },
    getLanguage(): string {
      return this.language
    },
    getExp(): number {
      return this.exp
    },
    getTime(): number {
      return this.time
    },
    isAdmin(): boolean {
      return this.weight === 1
    },
    getWeight(): number {
      return this.weight
    },
    isSpaceAdmin(): boolean {
      return this.uid === '1' || !!this.weight
    },
    getRegisterType(): number {
      return this.register_type
    },
  },
  actions: {
    async login(formData: { username: string; password: string }) {
      const res: any = await AuthApi.login(formData)
      this.setToken(res.access_token)
    },

    async logout() {
      try {
        // 调用后端登出接口
        await AuthApi.logout()
      } catch (error) {
        console.error('Logout API error:', error)
        // 即使接口调用失败，也继续执行登出流程
      }
      // 清除本地状态
      this.clear()
    },

    async info() {
      const res: any = await AuthApi.info()
      const res_data = res || {}

      const keys = ['uid', 'account', 'name', 'oid', 'language', 'exp', 'time', 'weight', 'register_type'] as const

      keys.forEach((key) => {
        const dkey = key === 'uid' ? 'id' : key
        const value = res_data[dkey]
        if (key === 'exp' || key === 'time' || key === 'weight' || key === 'register_type') {
          this[key] = Number(value || 0)
        } else {
          this[key] = String(value)
        }
        wsCache.set('user.' + key, value)
      })

      this.setLanguage(this.language)
    },
    setToken(token: string) {
      wsCache.set('user.token', token)
      this.token = token
    },
    setExp(exp: number) {
      wsCache.set('user.exp', exp)
      this.exp = exp
    },
    setTime(time: number) {
      wsCache.set('user.time', time)
      this.time = time
    },
    setUid(uid: string) {
      wsCache.set('user.uid', uid)
      this.uid = uid
    },
    setAccount(account: string) {
      wsCache.set('user.account', account)
      this.account = account
    },
    setName(name: string) {
      wsCache.set('user.name', name)
      this.name = name
    },
    setOid(oid: string) {
      wsCache.set('user.oid', oid)
      this.oid = oid
    },
    setLanguage(language: string) {
      if (!language) {
        language = 'zh-CN'
      } else if (language === 'zh_CN') {
        language = 'zh-CN'
      } else if (language === 'ko_KR') {
        language = 'ko-KR'
      }
      wsCache.set('user.language', language)
      this.language = language
      i18n.global.locale.value = language
      /* const { locale } = useI18n()
      locale.value = language */
      // locale.setLang(language)
    },
    setWeight(weight: number) {
      wsCache.set('user.weight', weight)
      this.weight = weight
    },
    clear() {
      const keys: string[] = [
        'token',
        'uid',
        'account',
        'name',
        'oid',
        'language',
        'exp',
        'time',
        'weight',
        'register_type',
      ]
      keys.forEach((key) => wsCache.delete('user.' + key))
      this.$reset()
    },
  },
})

export const useUserStore = () => {
  return UserStore(store)
}
