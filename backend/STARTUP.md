# SQLBot 项目启动指南

## 前置要求

### 1. Python 环境
- Python 3.11（必须）
- 推荐使用虚拟环境

### 2. 数据库
- PostgreSQL（推荐，默认）
- 或 MySQL、SQL Server、Oracle 等（需配置）

### 3. 可选依赖
- Redis（用于缓存，可选，默认使用内存缓存）
- 向量数据库扩展：pgvector（如使用 PostgreSQL）

## 安装步骤

### 1. 创建虚拟环境（推荐）

```bash
# 使用 Python 3.11
python3.11 -m venv venv

# 激活虚拟环境
# macOS/Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate
```

### 2. 安装依赖

项目使用 `uv` 或 `pip` 管理依赖：

```bash
# 使用 uv（推荐，更快）
uv pip install -e .

# 或使用 pip
pip install -e .

# 如果需要 GPU 支持（可选）
uv pip install -e ".[cu128]"  # CUDA 12.8
# 或
uv pip install -e ".[cpu]"    # CPU 版本
```

### 3. 配置环境变量

在项目根目录（`sqlbot-1.2.0/`）创建 `.env` 文件：

```bash
# 数据库配置
POSTGRES_SERVER=localhost
POSTGRES_PORT=5432
POSTGRES_USER=root
POSTGRES_PASSWORD=Password123@pg
POSTGRES_DB=sqlbot

# 或者使用 MySQL（取消注释并配置）
# SQLBOT_DB_URL=mysql+pymysql://root:Password123%40mysql@127.0.0.1:3306/sqlbot

# 前端地址（用于 CORS）
FRONTEND_HOST=http://localhost:5173
BACKEND_CORS_ORIGINS=http://localhost:5173,http://localhost:3000

# 缓存配置（可选）
CACHE_TYPE=memory  # 或 redis
# CACHE_REDIS_URL=redis://localhost:6379/0

# 日志配置
LOG_LEVEL=INFO
LOG_DIR=logs

# 文件上传目录
UPLOAD_DIR=/opt/sqlbot/data/file
EXCEL_PATH=/opt/sqlbot/data/excel

# MCP 配置
MCP_IMAGE_PATH=/opt/sqlbot/images
MCP_IMAGE_HOST=http://localhost:3000

# Embedding 配置
DEFAULT_EMBEDDING_MODEL=shibing624/text2vec-base-chinese
EMBEDDING_ENABLED=true
EMBEDDING_DEFAULT_SIMILARITY=0.4

# 许可证配置（可选）
SQLBOT_KEY_EXPIRED=100
```

### 4. 初始化数据库

确保 PostgreSQL 已安装并运行：

```bash
# 创建数据库
createdb sqlbot

# 如果使用 pgvector，需要安装扩展
psql -d sqlbot -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 5. 创建必要的目录

```bash
# 在项目根目录创建必要的目录
mkdir -p /opt/sqlbot/data/file
mkdir -p /opt/sqlbot/data/excel
mkdir -p /opt/sqlbot/images
mkdir -p /opt/sqlbot/models
mkdir -p logs
```

或者在本地创建（修改配置中的路径）：

```bash
mkdir -p data/file
mkdir -p data/excel
mkdir -p images
mkdir -p models
mkdir -p logs
```

## 启动项目

### 方式一：直接运行（开发模式）

```bash
# 进入 backend 目录
cd backend

# 运行主程序
python main.py
```

或者使用 uvicorn：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 方式二：使用脚本启动

```bash
# 运行预启动脚本（如果需要）
bash scripts/prestart.sh

# 然后启动应用
python main.py
```

## 验证启动

启动成功后，你应该看到：

1. 数据库迁移日志
2. 缓存初始化日志
3. Embedding 数据初始化日志
4. "✅ SQLBot 初始化完成" 消息

访问以下地址验证：

- API 文档: http://localhost:8000/api/v1/openapi.json
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 常见问题

### 1. 数据库连接失败

**问题**: `sqlalchemy.exc.OperationalError`

**解决方案**:
- 检查数据库服务是否运行
- 验证 `.env` 中的数据库配置
- 确保数据库已创建

### 2. 依赖安装失败

**问题**: `torch` 或其他包安装失败

**解决方案**:
- 使用 `uv` 而非 `pip`（更快更可靠）
- 检查 Python 版本是否为 3.11
- 对于 torch，根据系统选择 `[cpu]` 或 `[cu128]` 额外依赖

### 3. 端口被占用

**问题**: `Address already in use`

**解决方案**:
- 修改 `main.py` 中的端口号
- 或使用 `lsof -ti:8000 | xargs kill` 杀死占用端口的进程

### 4. 权限错误

**问题**: 无法创建目录或文件

**解决方案**:
- 修改配置中的路径为当前用户可写的目录
- 或使用 `sudo`（不推荐）

### 5. pgvector 扩展未安装

**问题**: `extension "vector" does not exist`

**解决方案**:
```bash
# 安装 pgvector
# macOS:
brew install pgvector

# 或在 PostgreSQL 中安装
psql -d sqlbot -c "CREATE EXTENSION vector;"
```

## 开发模式

启动时会自动：
- ✅ 运行数据库迁移（Alembic）
- ✅ 初始化缓存
- ✅ 初始化 Embedding 数据
- ✅ 加载动态 CORS 配置

## 生产部署

生产环境建议：

1. 使用 `gunicorn` + `uvicorn` workers
2. 配置反向代理（Nginx）
3. 使用 Redis 作为缓存
4. 配置 HTTPS
5. 设置环境变量而非使用 `.env` 文件
6. 使用进程管理工具（systemd, supervisor 等）

```bash
# 生产环境启动示例
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

## 相关命令

```bash
# 数据库迁移
alembic upgrade head          # 升级到最新版本
alembic downgrade -1          # 回退一个版本
alembic revision --autogenerate -m "message"  # 生成新迁移

# 代码格式化
bash scripts/format.sh

# 代码检查
bash scripts/lint.sh

# 运行测试
bash scripts/test.sh
```

## 默认账号

根据配置，默认管理员密码为：`SQLBot@123456`

首次启动后需要通过 API 创建管理员账号。

