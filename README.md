# SQLBot - 智能问数小助手

SQLBot 是一个智能问数助手，可以根据用户的自然语言提问，自动生成 SQL 语句并创建可视化图表。

## 项目结构

```
.
├── backend/          # 后端服务 (FastAPI + Python)
├── frontend/         # 前端应用 (Vue 3 + TypeScript)
├── g2-ssr/           # G2 图表服务端渲染
└── data/             # 数据文件目录
```

## 技术栈

### 后端
- **框架**: FastAPI
- **Python**: 3.11+
- **数据库**: PostgreSQL (支持 MySQL、SQL Server、Oracle 等)
- **ORM**: SQLModel
- **AI**: LangChain, OpenAI, 向量数据库 (pgvector)
- **缓存**: Redis / 内存缓存

### 前端
- **框架**: Vue 3
- **语言**: TypeScript
- **构建工具**: Vite
- **UI 组件**: Element Plus
- **图表**: AntV G2, AntV S2

## 快速开始

### 后端启动

详细的后端启动说明请参考 [backend/STARTUP.md](backend/STARTUP.md)

```bash
cd backend
# 安装依赖
uv pip install -e .

# 配置环境变量（创建 .env 文件）
# 初始化数据库
alembic upgrade head

# 启动服务
python main.py
```

### 前端启动

```bash
cd frontend
# 安装依赖
npm install

# 开发模式
npm run dev

# 构建生产版本
npm run build
```

## 功能特性

- 🤖 **智能 SQL 生成**: 基于自然语言自动生成 SQL 查询语句
- 📊 **可视化图表**: 自动推荐并生成适合的图表类型
- 🔍 **多数据库支持**: PostgreSQL、MySQL、SQL Server、Oracle 等
- 💬 **对话式交互**: 支持多轮对话，上下文理解
- 📝 **术语管理**: 自定义业务术语和同义词
- 🎯 **数据训练**: 支持数据训练提升 SQL 生成准确性
- 🔐 **权限控制**: 行级和列级数据权限管理

## 环境要求

- Python 3.11+
- Node.js 16+
- PostgreSQL 12+ (推荐)
- Redis (可选，用于缓存)

## 许可证

详见 [LICENSE](LICENSE) 文件

## 贡献

欢迎提交 Issue 和 Pull Request！

## 相关文档

- [后端启动指南](backend/STARTUP.md)
- [数据库迁移指南](backend/ALEMBIC_GUIDE.md)
- [故障排查](backend/STARTUP_TROUBLESHOOTING.md)

