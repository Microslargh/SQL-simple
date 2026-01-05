# 启动问题排查指南

## 问题：启动脚本可以启动，但命令行无法启动

### 原因分析

启动脚本 `start_backend.sh` 做了以下关键操作：

1. **切换到正确的目录**：`cd "$BACKEND_DIR"` (backend 目录)
2. **激活虚拟环境**：`source venv/bin/activate`
3. **设置工作目录**：在 backend 目录下运行 `python main.py`

### 常见问题

#### 1. 工作目录不正确

**问题**：`main.py` 中有相对路径引用，必须在 `backend` 目录下运行。

**错误示例**：
```bash
# ❌ 错误：在项目根目录运行
cd /path/to/sqlbot-1.2.0
python backend/main.py  # 会失败
```

**正确方式**：
```bash
# ✅ 正确：先切换到 backend 目录
cd /path/to/sqlbot-1.2.0/backend
python main.py
```

#### 2. 虚拟环境未激活

**问题**：没有激活虚拟环境，Python 找不到已安装的依赖。

**错误示例**：
```bash
# ❌ 错误：没有激活虚拟环境
cd backend
python main.py  # 可能找不到 fastapi, uvicorn 等模块
```

**正确方式**：
```bash
# ✅ 正确：激活虚拟环境
cd backend
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows
python main.py
```

#### 3. 找不到 alembic.ini

**问题**：`main.py` 中的 `run_migrations()` 函数使用相对路径 `"alembic.ini"`，必须在 backend 目录下运行。

**错误信息**：
```
FileNotFoundError: alembic.ini not found
```

**解决方案**：确保在 `backend` 目录下运行。

#### 4. 找不到 .env 文件

**问题**：配置文件路径是相对于 backend 目录的。

**配置位置**：`backend/common/core/config.py` 中配置为：
```python
env_file="../data/file/config/.env"
```

这意味着 `.env` 文件应该在项目根目录的 `data/file/config/.env`。

**解决方案**：
```bash
# 确保 .env 文件在正确位置
ls -la ../data/file/config/.env
```

#### 5. PYTHONPATH 未设置

**问题**：如果不在 backend 目录下运行，Python 可能找不到模块。

**解决方案**：
```bash
# 方式1：在 backend 目录下运行（推荐）
cd backend
python main.py

# 方式2：设置 PYTHONPATH
export PYTHONPATH=/path/to/sqlbot-1.2.0/backend:$PYTHONPATH
cd backend
python main.py
```

## 正确的启动方式

### 方式一：使用启动脚本（推荐）

```bash
# 在项目根目录
./start_backend.sh
```

### 方式二：手动启动（命令行）

```bash
# 1. 切换到 backend 目录
cd backend

# 2. 激活虚拟环境
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

# 3. 运行主程序
python main.py
```

### 方式三：使用 uvicorn 直接启动

```bash
# 1. 切换到 backend 目录
cd backend

# 2. 激活虚拟环境
source venv/bin/activate

# 3. 使用 uvicorn 启动
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## 快速诊断命令

运行以下命令检查环境：

```bash
# 1. 检查当前目录
pwd
# 应该显示：/path/to/sqlbot-1.2.0/backend

# 2. 检查虚拟环境是否激活
which python
# 应该显示：/path/to/sqlbot-1.2.0/backend/venv/bin/python

# 3. 检查 Python 版本
python --version
# 应该显示：Python 3.11.x 或 3.12.x

# 4. 检查关键模块是否可导入
python -c "import fastapi; import uvicorn; print('OK')"
# 应该输出：OK

# 5. 检查 alembic.ini 是否存在
ls -la alembic.ini
# 应该显示文件存在

# 6. 检查 .env 文件
ls -la ../data/file/config/.env
# 应该显示文件存在（如果配置了的话）
```

## 常见错误及解决方案

### 错误1：ModuleNotFoundError

```
ModuleNotFoundError: No module named 'fastapi'
```

**原因**：虚拟环境未激活或依赖未安装。

**解决**：
```bash
cd backend
source venv/bin/activate
pip install -e .
```

### 错误2：FileNotFoundError: alembic.ini

```
FileNotFoundError: [Errno 2] No such file or directory: 'alembic.ini'
```

**原因**：不在 backend 目录下运行。

**解决**：
```bash
cd backend
python main.py
```

### 错误3：数据库连接失败

```
sqlalchemy.exc.OperationalError: could not connect to server
```

**原因**：数据库未启动或配置错误。

**解决**：
1. 检查数据库服务是否运行
2. 检查 `.env` 文件中的数据库配置
3. 确保数据库已创建

### 错误4：端口被占用

```
OSError: [Errno 48] Address already in use
```

**解决**：
```bash
# 查找占用端口的进程
lsof -ti:8000

# 杀死进程
kill $(lsof -ti:8000)
```

## 一键启动脚本（简化版）

如果经常需要手动启动，可以创建一个简化的启动脚本：

```bash
#!/bin/bash
# 保存为 backend/start.sh

cd "$(dirname "$0")"
source venv/bin/activate
python main.py
```

然后运行：
```bash
cd backend
bash start.sh
```

## 总结

**关键点**：
1. ✅ 必须在 `backend` 目录下运行
2. ✅ 必须激活虚拟环境
3. ✅ 确保 `.env` 文件在正确位置
4. ✅ 确保数据库服务运行

**推荐**：使用 `start_backend.sh` 脚本启动，它会自动处理所有这些问题。

