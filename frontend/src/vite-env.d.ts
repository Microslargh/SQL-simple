/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
  readonly VITE_LOGOUT_URL?: string
  readonly LOGOUT_URL?: string  // 支持不带VITE_前缀的环境变量（通过vite.config.ts的define配置）
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module 'less/lib/less/functions/color.js' {
  const content: any
  export default content
}
declare module 'less/lib/less/tree/color.js' {
  const content: any
  export default content
}

// LicenseGenerator 全局类型声明
/*declare global {
  interface LicenseGenerator {
    generate(): string
    sqlbotEncrypt(data: string): string
    generateRouters(router: any): void
    init(baseUrl: string): Promise<void>
    getLicense(): any
  }

  const LicenseGenerator: LicenseGenerator
}*/

export {}
