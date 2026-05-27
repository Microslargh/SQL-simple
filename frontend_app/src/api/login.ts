// import { request } from '@/utils/request'
// export const AuthApi = {
//   login: (credentials: { username: string; password: string }) => {
//     const entryCredentials = {
//       username: LicenseGenerator.sqlbotEncrypt(credentials.username),
//       password: LicenseGenerator.sqlbotEncrypt(credentials.password),
//     }
//     return request.post<{
//       data: any
//       token: string
//     }>('/login/access-token', entryCredentials, {
//       headers: {
//         'Content-Type': 'application/x-www-form-urlencoded',
//       },
//     })
//   },
//   logout: () => request.post('/auth/logout'),
//   info: () => request.get('/user/info'),
// }


import { request } from '@/utils/request'
export const AuthApi = {
  login: (credentials: { username: string; password: string }) => {
    const entryCredentials = {
      username: LicenseGenerator.sqlbotEncrypt(credentials.username),
      password: LicenseGenerator.sqlbotEncrypt(credentials.password),
    }
    // 后端 OAuth2PasswordRequestForm 需要 application/x-www-form-urlencoded 表单项，必须用 URLSearchParams 序列化
    const body = new URLSearchParams({
      username: entryCredentials.username,
      password: entryCredentials.password,
    }).toString()
    return request.post<{
      data: any
      token: string
    }>('/login/access-token', body, {
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
    })
  },
  logout: () => request.post('/auth/logout'),
  info: () => request.get('/user/info'),
}
