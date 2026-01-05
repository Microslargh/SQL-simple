# Alembic 数据库迁移工具使用指南

## 概述

Alembic 是 SQLAlchemy 的数据库迁移工具，用于管理数据库 schema 的版本控制。在 SQLBot 项目中，Alembic 用于管理数据库结构的变更历史。

## 目录结构

```
backend/
├── alembic/                    # Alembic 主目录
│   ├── env.py                 # 环境配置文件（核心文件）
│   ├── script.py.mako         # 迁移文件模板
│   ├── README                 # 说明文件
│   └── versions/              # 迁移脚本目录
│       ├── 001_ddl.py         # 第一个迁移脚本
│       ├── 002_ddl_autogenerate.py
│       ├── ...
│       └── 047_add_user_register_type.py  # 最新的迁移脚本
└── alembic.ini                 # Alembic 配置文件
```

## 核心文件说明

### 1. `alembic.ini` - 配置文件

这是 Alembic 的主配置文件，定义了：
- 迁移脚本的位置：`script_location = alembic`
- 文件命名模板：`file_template = %%(slug)s`
- 日志配置

**关键配置**：
```ini
[alembic]
script_location = alembic          # 迁移脚本目录
file_template = %%(slug)s          # 文件名格式（只使用描述，不使用版本号）
```

### 2. `alembic/env.py` - 环境配置

这是 Alembic 的核心文件，负责：
- 连接数据库
- 加载模型元数据
- 配置迁移环境

**关键代码**：
```python
# 导入模型（用于自动生成迁移）
from apps.terminology.models.terminology_model import SQLModel
target_metadata = SQLModel.metadata

# 获取数据库连接 URL
def get_url():
    return str(settings.SQLALCHEMY_DATABASE_URI)
```

**注意**：当前只导入了 `terminology` 模型，如果需要自动生成其他模型的迁移，需要在这里导入。

### 3. `alembic/script.py.mako` - 模板文件

这是生成新迁移文件的模板，定义了迁移文件的基本结构。

### 4. `alembic/versions/` - 迁移脚本目录

包含所有的数据库迁移脚本，按时间顺序命名。

## 迁移文件结构

每个迁移文件都包含以下部分：

```python
"""迁移描述

Revision ID: 唯一版本ID
Revises: 上一个版本的ID
Create Date: 创建时间

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'a7b8c9d0e1f2'      # 当前版本ID
down_revision = '8855aea2dd61'  # 上一个版本ID
branch_labels = None
depends_on = None

def upgrade():
    """升级数据库：执行此迁移"""
    op.add_column('sys_user', sa.Column('register_type', sa.Integer(), ...))

def downgrade():
    """降级数据库：回滚此迁移"""
    op.drop_column('sys_user', 'register_type')
```

## 常用命令

### 1. 查看当前版本

```bash
cd backend
alembic current
```

### 2. 查看迁移历史

```bash
# 查看所有迁移
alembic history

# 查看详细信息
alembic history --verbose
```

### 3. 升级数据库

```bash
# 升级到最新版本
alembic upgrade head

# 升级到指定版本
alembic upgrade <revision_id>

# 升级一个版本
alembic upgrade +1
```

### 4. 降级数据库

```bash
# 降级一个版本
alembic downgrade -1

# 降级到指定版本
alembic downgrade <revision_id>

# 降级到基础版本
alembic downgrade base
```

### 5. 生成新迁移

```bash
# 自动生成迁移（基于模型变更）
alembic revision --autogenerate -m "描述信息"

# 手动创建空迁移
alembic revision -m "描述信息"
```

### 6. 查看待执行的迁移

```bash
alembic show head
```

## 工作流程

### 场景1：修改模型后生成迁移

1. **修改模型文件**（如 `apps/system/models/user.py`）
   ```python
   class UserModel(SQLModel, table=True):
       # 添加新字段
       register_type: int = Field(default=0)
   ```

2. **确保模型已导入到 env.py**
   ```python
   # 在 alembic/env.py 中导入
   from apps.system.models.user import UserModel
   ```

3. **生成迁移**
   ```bash
   cd backend
   alembic revision --autogenerate -m "add_user_register_type"
   ```

4. **检查生成的迁移文件**
   - 打开 `alembic/versions/xxx_add_user_register_type.py`
   - 检查 `upgrade()` 和 `downgrade()` 函数是否正确

5. **执行迁移**
   ```bash
   alembic upgrade head
   ```

### 场景2：手动创建迁移

当自动生成无法满足需求时，可以手动创建：

```bash
alembic revision -m "custom_migration"
```

然后编辑生成的迁移文件，手动编写 SQL 操作。

## 迁移文件示例

### 示例1：添加字段

```python
def upgrade():
    op.add_column('sys_user', 
        sa.Column('register_type', sa.Integer(), 
                  server_default=sa.text('0'), 
                  nullable=False))

def downgrade():
    op.drop_column('sys_user', 'register_type')
```

### 示例2：创建表

```python
def upgrade():
    op.create_table(
        'sys_user',
        sa.Column('id', sa.BIGINT, primary_key=True),
        sa.Column('account', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        # ... 更多字段
    )

def downgrade():
    op.drop_table('sys_user')
```

### 示例3：修改字段

```python
def upgrade():
    op.alter_column('sys_user', 'email',
        existing_type=sa.String(255),
        type_=sa.String(500),
        nullable=True)

def downgrade():
    op.alter_column('sys_user', 'email',
        existing_type=sa.String(500),
        type_=sa.String(255),
        nullable=True)
```

## 在项目中的使用

### 自动执行迁移

在 `main.py` 中，应用启动时会自动执行迁移：

```python
def run_migrations():
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()  # 启动时自动执行
    # ...
```

这意味着每次启动应用时，数据库会自动升级到最新版本。

## 最佳实践

### 1. 迁移文件命名

- 使用描述性的名称
- 格式：`序号_描述.py`（如 `047_add_user_register_type.py`）

### 2. 迁移内容

- **总是提供 `downgrade()` 函数**：确保可以回滚
- **测试迁移**：在开发环境先测试
- **数据迁移**：如果需要迁移数据，在 `upgrade()` 中添加数据迁移逻辑

### 3. 版本控制

- **提交迁移文件**：将迁移文件提交到 Git
- **不要修改已执行的迁移**：如果迁移已应用到生产环境，不要修改
- **创建新迁移**：需要修改时，创建新的迁移文件

### 4. 生产环境

- **备份数据库**：执行迁移前先备份
- **测试降级**：确保 `downgrade()` 可以正常工作
- **逐步升级**：不要跳过多个版本

## 常见问题

### Q1: 自动生成迁移时没有检测到变更？

**原因**：模型没有导入到 `env.py`

**解决**：在 `alembic/env.py` 中导入相关模型：
```python
from apps.system.models.user import UserModel
from apps.chat.models.chat_model import ChatModel
# ... 导入所有需要迁移的模型
target_metadata = SQLModel.metadata
```

### Q2: 迁移失败怎么办？

**解决**：
1. 检查错误信息
2. 修复迁移文件
3. 如果已经部分执行，可能需要手动修复数据库
4. 使用 `alembic downgrade -1` 回滚（如果可能）

### Q3: 如何查看迁移状态？

```bash
# 查看当前版本
alembic current

# 查看历史
alembic history

# 查看待执行的迁移
alembic show head
```

### Q4: 如何合并多个迁移？

不建议合并已执行的迁移。如果需要，可以：
1. 创建新的迁移文件
2. 在新迁移中执行所有需要的操作

## 迁移脚本示例分析

### 001_ddl.py - 初始数据库结构

这是第一个迁移，创建了基础表结构：
- 创建 `sys_user` 表
- 插入默认管理员用户

### 047_add_user_register_type.py - 添加注册类型字段

```python
def upgrade():
    # 添加 register_type 字段，默认值为 0（系统注册）
    op.add_column('sys_user', 
        sa.Column('register_type', sa.Integer(), 
                  server_default=sa.text('0'), 
                  nullable=False))

def downgrade():
    # 回滚时删除该字段
    op.drop_column('sys_user', 'register_type')
```

## 相关脚本

项目中有一些辅助脚本：

- `scripts/alembic/auto.sh` - 自动生成迁移
- `scripts/alembic/exec.sh` - 执行迁移

## 总结

Alembic 是 SQLBot 项目中管理数据库结构变更的核心工具：

1. **版本控制**：跟踪数据库结构的所有变更
2. **自动化**：应用启动时自动执行迁移
3. **可回滚**：支持升级和降级
4. **团队协作**：迁移文件可以版本控制，团队共享

通过 Alembic，可以安全、可控地管理数据库结构的演进。

