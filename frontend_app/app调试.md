# 移动端前端调试记录

## 现象

从手机钉钉端打开应用，只显示标题"财务智能问数"，其余空白。后端无任何请求日志。部署旧 dist 包则正常工作。

## 根本原因

两个问题叠加：

### 1. 路由守卫 `beforeEach` 中未捕获异常导致 Vue 永远不挂载

`src/router/watch.ts` 的 `beforeEach` 中有三步串行 `await`：

```
autoLogin() → loadXpackStatic() → setAppearance() → generateRouters()
```

其中任意一步抛异常（如 `LicenseGenerator` 未定义、网络请求失败），`beforeEach` 就 reject，Vue Router 取消导航，`next()` 永远不调用 —— 页面始终停留在 HTML 壳。

### 2. 路由组件全部 eager import，模块初始化即崩溃

`src/router/index.ts` 中 20+ 个路由组件全部用静态 `import` 引入。这些组件及其依赖链在模块加载阶段就执行，钉钉 webview 环境中某个模块初始化时抛出未捕获异常，导致整个 app 初始化失败。

旧包只有 4 条路由且都是懒加载，不会触发此问题。

## 具体修改

### `src/router/watch.ts`

- **硬编码生产 corpId** → `dingfb48100d0e532caa24f2f5cc6abecb85`，去掉 `import.meta.env.MODE` 条件判断
- **所有初始化步骤加 try-catch** → `loadXpackStatic()`、`setAppearance()`、`LicenseGenerator.generateRouters()`、`userStore.info()` 全部包裹，失败不阻塞导航
- **`autoLogin()` 改为 fire-and-forget** → 去掉 `await`，不阻塞路由守卫
- **加 `typeof LicenseGenerator !== 'undefined'` 守卫** → 防止未定义时调用崩溃

### `src/router/index.ts`

- **15+ 个非核心路由组件改为懒加载** → `component: () => import('...')`，避免模块级初始化错误拖垮整个 app
- 仅保留 `login`、`chat`、`Page401` 和布局组件为 eager import

### `src/views/chat/index.vue`

- **`startChatDsId` 默认值 `10` → `undefined`** → 没有 `start_chat` 查询参数时不自动发起无效的创建会话请求

### `package.json`

- **补了 `dingtalk-jsapi` 和 `js-audio-recorder` 依赖** → 原 `node_modules` 中有但未声明在 `package.json`，`npm install` 后丢失导致编译报错

## 认证流程

移动端认证链路与桌面端不同：

```
桌面端: 钉钉 → P OST /api/v1/login/dingtalk-login → SQLBot 后端 → token
移动端: 钉钉 JSAPI → requestAuthCode → /fin/cud/other/quickAuthen → 外部网关 → token
```

移动端调用的是 `/fin/cud/other/quickAuthen`，由 CGNPC 网关处理钉钉免登并返回 token，不走 SQLBot 的 `/api/v1/login/dingtalk-login`。
