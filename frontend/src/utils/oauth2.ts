/**
 * OAuth2工具函数
 */
import { request } from '@/utils/request'

interface OAuth2Config {
  enabled: boolean
  authorization_url: string
  client_id: string
  redirect_uri: string
  scope: string
  response_type: string
  logout_url?: string  // OAuth2登出地址
}

let cachedConfig: OAuth2Config | null = null

/**
 * 获取OAuth2配置
 */
export async function getOAuth2Config(): Promise<OAuth2Config | null> {
  console.log('getOAuth2Config')
  if (cachedConfig) {
    return cachedConfig
  }

  try {
    // 后端的响应拦截器会自动处理 {code: 0, data: {...}, msg: null} 格式
    // 返回的 response 已经是 data 部分
    const config = await request.get<OAuth2Config>('/oauth2/config')

    // 检查配置是否有效
    if (!config) {
      console.warn('OAuth2 config is null or undefined')
      return null
    }

    // 检查是否启用且配置完整
    if (!config.enabled) {
      console.log('OAuth2 is not enabled')
      return null
    }

    // 检查必需字段
    if (!config.authorization_url || config.authorization_url.trim() === '') {
      console.warn('OAuth2 authorization_url is not configured. Please set OAUTH2_AUTHORIZATION_URL in .env file')
      return null
    }

    if (!config.client_id || config.client_id.trim() === '') {
      console.warn('OAuth2 client_id is not configured. Please set OAUTH2_CLIENT_ID in .env file')
      return null
    }

    cachedConfig = config
    return config
  } catch (error) {
    console.error('Failed to get OAuth2 config:', error)
    return null
  }
}

/**
 * 生成随机state参数
 */
function generateState(): string {
  // 使用crypto API生成随机state，如果不可用则使用Math.random
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    const array = new Uint8Array(32)
    crypto.getRandomValues(array)
    return btoa(String.fromCharCode(...array))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=/g, '')
  } else {
    // 降级方案：使用Math.random
    const randomBytes = new Array(32)
    for (let i = 0; i < 32; i++) {
      randomBytes[i] = Math.floor(Math.random() * 256)
    }
    return btoa(String.fromCharCode(...randomBytes))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=/g, '')
  }
}

/**
 * 构建OAuth2授权URL（从后端获取，包含state参数）
 * 后端会生成并缓存state，用于回调时验证
 */
export async function buildOAuth2AuthUrl(): Promise<string | null> {
  try {
    // 从后端获取包含state的授权URL
    const response = await request.get<{ auth_url: string }>('/oauth2/auth-url')
    if (response && response.auth_url) {
      console.log('Got OAuth2 auth URL from backend')
      return response.auth_url
    }
    return null
  } catch (error) {
    console.error('Failed to get OAuth2 auth URL from backend:', error)
    // 如果后端接口失败，降级到使用配置（但不推荐，因为无法验证state）
    const config = await getOAuth2Config()
    if (!config || !config.enabled || !config.authorization_url) {
      return null
    }
    // 降级方案：使用前端生成的state（不推荐，但为了兼容性保留）
    console.warn('Falling back to frontend-generated state (not recommended)')
    const state = generateState()
    const params = new URLSearchParams({
      client_id: config.client_id,
      redirect_uri: config.redirect_uri,
      response_type: config.response_type,
      scope: config.scope,
      state: state,
    })
    return `${config.authorization_url}?${params.toString()}`
  }
}

/**
 * 跳转到OAuth2登录页面
 */
export async function redirectToOAuth2Login(): Promise<void> {
  const authUrl = await buildOAuth2AuthUrl()

  if (authUrl) {
    console.log('Redirecting to OAuth2 login:', authUrl)
    window.location.href = authUrl
  } else {
    // 如果OAuth2未启用或配置无效，跳转到登录页
    console.warn('OAuth2 is not properly configured, redirecting to login page')
    window.location.href = '/#/login'
  }
}

/**
 * 构建OAuth2登出URL
 */
export async function buildOAuth2LogoutUrl(): Promise<string | null> {
  const config = await getOAuth2Config()

  if (!config || !config.enabled || !config.logout_url) {
    return null
  }

  // 构建登出URL，添加回调参数
  const params = new URLSearchParams({
    redirect_uri: window.location.origin + '/#/login'
  })

  return `${config.logout_url}?${params.toString()}`
}

/**
 * 跳转到OAuth2登出页面
 */
export async function redirectToOAuth2Logout(): Promise<void> {
  const logoutUrl = await buildOAuth2LogoutUrl()

  if (logoutUrl) {
    console.log('Redirecting to OAuth2 logout:', logoutUrl)
    window.location.href = logoutUrl
  } else {
    // 如果OAuth2未启用或没有配置登出URL，直接跳转到登录页
    console.warn('OAuth2 logout URL not configured, redirecting to login page')
    window.location.href = '/#/login'
  }
}

