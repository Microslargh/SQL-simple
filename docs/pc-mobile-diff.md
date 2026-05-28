# PC端与移动端前端代码差异清单

## 概述

`frontend/`（PC端）与 `frontend_app/`（移动端）是两个独立的 Vue 3 + TypeScript 前端项目，共享相似的业务逻辑但针对不同设备进行了优化。

---

## 一、项目配置差异

### 1.1 入口 HTML

| 对比项 | PC端 (`frontend/index.html`) | 移动端 (`frontend_app/index.html`) |
|--------|------------------------------|-----------------------------------|
| 视口配置 | `<meta name="viewport" content="width=device-width, initial-scale=1.0" />` | `<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, minimum-scale=1.0, user-scalable=no, viewport-fit=cover">` |
| 调试工具 | 无 | 集成钉钉远程调试 `@ali/dingtalk-h5-remote-debug` |
| 标题 | 财务智能问数 | 财务智能问数 |

### 1.2 Vite 配置

| 对比项 | PC端 | 移动端 |
|--------|------|--------|
| **开发服务器** | 无代理配置 | 配置多个代理：`/fin/cud`、`/api/v1`、`/tuling/asrc/v3/` |
| **路由模式** | 标准模式 | `createWebHashHistory()` 哈希模式 |
| **构建输出** | `dist/` | `dist/` |
| **特殊插件** | 无 | `unplugin-auto-import`、`unplugin-vue-components-secondary` |

### 1.3 环境变量

| 环境 | PC端 | 移动端 |
|------|------|--------|
| **开发环境** | `VITE_API_BASE_URL=http://10.125.33.145:3100/api/v1` | `VITE_API_BASE_URL=http://10.125.33.145:9038/api/v1` |
| **生产环境** | `VITE_API_BASE_URL=http://10.125.33.145:3100/api/v1` | `VITE_API_BASE_URL=https://newmobileapp.cgnpc.com.cn/apphost/FIN/api/v1` |
| **额外变量** | 无 | `VITE_API_BASE_URLR=https://newmobileapp.cgnpc.com.cn/apphost/FIN` |

---

## 二、技术栈差异

### 2.1 依赖对比

| 依赖 | PC端版本 | 移动端版本 | 说明 |
|------|----------|------------|------|
| `element-plus` | ^2.10.1 | ^2.10.1 | 基础UI组件库 |
| `element-plus-secondary` | ^1.0.0 | ^1.0.0 | 移动端适配版本 |
| `@antv/g2` | ^5.3.3 | ^5.3.3 | 图表库 |
| `@antv/s2` | ^2.4.3 | ^2.4.3 | 表格组件 |
| `@antv/x6` | ^2.18.1 | ^2.18.1 | 画布组件 |

### 2.2 核心依赖版本一致

- Vue 3.5.13
- Vue Router 4.5.0
- Vue I18n 9.14.4
- Pinia 3.0.2
- Vite 6.3.1
- TypeScript ~5.7.2

---

## 三、功能模块差异

### 3.1 页面路由对比

| 模块 | PC端 | 移动端 | 差异说明 |
|------|------|--------|----------|
| **登录页** | ✅ | ✅ | 一致 |
| **聊天页** | ✅ | ✅ | 移动端有 `index copy.vue` 备份 |
| **数据源管理** | ✅ | ✅ | 一致 |
| **仪表板** | ✅ | ✅ | 一致 |
| **系统设置** | ✅ | ✅ | 一致 |
| **嵌入式管理** | ✅ | ✅ | 一致 |
| **用户管理** | ✅ | ✅ | 一致 |
| **工作空间** | ✅ | ✅ | 一致 |

### 3.2 移动端特有文件

```
frontend_app/
├── eruda.js                    # 移动端调试工具
├── dist_app/                   # 构建产物（移动端）
├── dist_app2601/               # 历史构建版本
└── xpack_static/               # 静态资源包
    └── license-generator.umd.js
```

### 3.3 组件差异

| 组件 | PC端 | 移动端 | 说明 |
|------|------|--------|------|
| `Language-selector` | ❌ | ✅ | 移动端语言选择器 |
| `ProcessStep.vue` | ❌ | ✅ | 移动端流程步骤组件 |
| `ChartPopover.vue` | ❌ | ✅ | 移动端图表弹出层 |
| `SQPreviewSingle2.vue` | ❌ | ✅ | 移动端预览组件 |

---

## 四、样式与布局差异

### 4.1 响应式适配

| 特性 | PC端 | 移动端 |
|------|------|--------|
| 布局方式 | 桌面端固定布局 | 移动端流式布局 |
| 字体大小 | 标准桌面字体 | 自适应移动端字体 |
| 触摸交互 | 鼠标为主 | 触摸友好 |
| 导航方式 | 侧边栏导航 | 底部/顶部导航 |

### 4.2 CSS 变量与主题

移动端使用 `element-plus-secondary` 分支版本，针对移动端进行了优化：
- 更小的默认字体尺寸
- 更紧凑的组件间距
- 优化的触摸目标大小

---

## 五、构建与部署差异

### 5.1 构建命令

| 命令 | PC端 | 移动端 |
|------|------|--------|
| 开发 | `npm run dev` | `npm run dev --host` |
| 构建 | `npm run build` | `npm run build` |
| 预览 | `npm run preview` | `npm run preview` |

### 5.2 部署路径

| 环境 | PC端 | 移动端 |
|------|------|--------|
| 开发 | `http://localhost:5173` | `http://localhost:5173` |
| 生产 | 自定义部署 | `https://newmobileapp.cgnpc.com.cn/apphost/FIN/` |

---

## 六、数据与 API 差异

### 6.1 API 端点

| 用途 | PC端 | 移动端 |
|------|------|--------|
| 主 API | `/api/v1` | `/api/v1` |
| 财务接口 | 无 | `/fin/cud` |
| 图灵接口 | 无 | `/tuling/asrc/v3/` |

### 6.2 数据处理

移动端针对网络环境进行了优化：
- 更小的请求 payload
- 更频繁的缓存策略
- 离线数据支持

---

## 七、性能优化差异

### 7.1 移动端优化策略

1. **代码分割**：按需加载组件
2. **图片优化**：使用 WebP 格式
3. **缓存策略**：本地存储常用数据
4. **懒加载**：延迟加载非关键资源
5. **打包优化**：tree-shaking 移除未使用代码

---

## 八、目录结构对比

### 8.1 PC端 (`frontend/`)

```
frontend/
├── public/                     # 静态资源
├── src/
│   ├── api/                    # API 调用
│   ├── assets/                 # 资源文件
│   ├── components/             # 组件
│   ├── entity/                 # 实体定义
│   ├── i18n/                   # 国际化
│   ├── router/                 # 路由
│   ├── stores/                 # 状态管理
│   ├── utils/                  # 工具函数
│   └── views/                  # 页面视图
└── dist/                       # 构建输出
```

### 8.2 移动端 (`frontend_app/`)

```
frontend_app/
├── public/                     # 静态资源
├── src/
│   ├── api/                    # API 调用（扩展）
│   ├── assets/                 # 资源文件（扩展）
│   ├── components/             # 组件（扩展）
│   ├── entity/                 # 实体定义
│   ├── i18n/                   # 国际化
│   ├── router/                 # 路由（哈希模式）
│   ├── stores/                 # 状态管理
│   ├── utils/                  # 工具函数（扩展）
│   └── views/                  # 页面视图（扩展）
├── dist/                       # 构建输出
├── dist_app/                   # 移动端构建产物
├── dist_app2601/               # 历史版本
├── eruda.js                    # 调试工具
└── xpack_static/               # 静态资源包
```

---

## 九、总结

| 维度 | PC端 | 移动端 |
|------|------|--------|
| **目标设备** | 桌面浏览器 | 移动设备（H5/小程序） |
| **交互方式** | 鼠标+键盘 | 触摸手势 |
| **布局策略** | 固定布局 | 响应式/流式布局 |
| **API 配置** | 单一后端 | 多后端支持 |
| **构建优化** | 标准构建 | 移动端特化优化 |
| **调试工具** | 浏览器开发者工具 | Eruda + 钉钉远程调试 |

---

## 十、代码同步建议

由于两个项目存在大量重复代码，建议采用以下策略：

1. **共享组件库**：提取公共组件到独立包
2. **API 层抽象**：统一 API 调用接口
3. **样式系统**：使用 CSS 变量实现主题切换
4. **构建脚本**：统一构建配置
5. **自动化同步**：使用脚本同步变更

---

*文档生成时间：2026-05-28*