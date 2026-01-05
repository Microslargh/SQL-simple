# Patch文件转文件结构工具使用指南

## 工具说明

提供了两个工具来处理patch文件：

1. **extract_files_from_patch.py** - 提取文件列表和目录结构
2. **generate_files_from_patch.py** - 根据patch文件生成完整的文件结构

## 工具1: extract_files_from_patch.py

### 功能
- 从patch文件中提取所有涉及的文件路径
- 区分新增文件和修改文件
- 生成文件树结构
- 创建目录结构

### 使用方法

```bash
# 基本用法：列出所有文件
python3 backend/scripts/extract_files_from_patch.py 登录优化.patch

# 分别显示新增和修改的文件
python3 backend/scripts/extract_files_from_patch.py 主要提交.patch --separate

# 生成文件树结构
python3 backend/scripts/extract_files_from_patch.py 主要提交.patch --tree

# 保存文件列表到文件
python3 backend/scripts/extract_files_from_patch.py 主要提交.patch --output-file files.txt

# 创建目录结构（不创建文件）
python3 backend/scripts/extract_files_from_patch.py 主要提交.patch --output-dir ./extracted --create-dirs

# 组合使用
python3 backend/scripts/extract_files_from_patch.py 主要提交.patch --separate --tree --output-file files.txt
```

### 输出示例

```
================================================================================
新增文件:
================================================================================
  + backend/alembic/versions/048_add_user_orgname.py
  + backend/alembic/versions/049_add_user_orgcode.py

================================================================================
修改文件:
================================================================================
  M backend/apps/system/api/auth.py
  M backend/apps/system/api/oauth2.py

================================================================================
目录结构:
================================================================================
└── backend
    ├── alembic
    │   └── versions
    │       ├── 048_add_user_orgname.py
    │       └── 049_add_user_orgcode.py
    └── apps
        └── system
            └── api
                ├── auth.py
                └── oauth2.py
```

## 工具2: generate_files_from_patch.py

### 功能
- 解析patch文件，提取文件路径
- **从本地项目目录复制完整文件**（不是patch片段）
- 创建完整的目录结构
- 生成变更摘要报告

### 使用方法

```bash
# 基本用法：显示文件列表和摘要
python3 backend/scripts/generate_files_from_patch.py 主要提交.patch

# 只列出文件（不复制）
python3 backend/scripts/generate_files_from_patch.py 主要提交.patch --list-only

# 从本地项目复制完整文件到目标目录（推荐）
python3 backend/scripts/generate_files_from_patch.py 主要提交.patch \
    --output-dir ./extracted \
    --source-dir .

# 指定源项目目录
python3 backend/scripts/generate_files_from_patch.py 主要提交.patch \
    --output-dir ./extracted \
    --source-dir /path/to/project

# 生成摘要报告
python3 backend/scripts/generate_files_from_patch.py 主要提交.patch \
    --summary summary.txt \
    --list-only

# 完整流程：复制文件并生成摘要
python3 backend/scripts/generate_files_from_patch.py 主要提交.patch \
    --output-dir ./extracted \
    --source-dir . \
    --summary summary.txt
```

### 输出示例

```
================================================================================
文件变更摘要
================================================================================

新增文件 (3):
  + backend/alembic/versions/048_add_user_orgname.py
  + backend/alembic/versions/049_add_user_orgcode.py
  + backend/common/utils/security_utils.py

修改文件 (11):
  M backend/apps/system/api/auth.py
  M backend/apps/system/api/callback.py
  M backend/apps/system/api/oauth2.py
  ...

================================================================================
总计: 14 个文件
================================================================================

正在创建文件结构到: ./extracted
--------------------------------------------------------------------------------
[DIR]     backend/alembic/versions/
[FILE]    backend/alembic/versions/048_add_user_orgname.py (1234 bytes)
[DIR]     backend/apps/system/api/
[FILE]    backend/apps/system/api/auth.py (placeholder)
...
```

## 使用场景

### 场景1: 快速查看patch涉及的文件

```bash
python3 backend/scripts/extract_files_from_patch.py 主要提交.patch --separate --tree
```

### 场景2: 提取文件列表用于代码审查

```bash
python3 backend/scripts/extract_files_from_patch.py 主要提交.patch \
    --output-file review_files.txt \
    --separate
```

### 场景3: 创建文件结构用于测试环境（复制完整文件）

```bash
python3 backend/scripts/generate_files_from_patch.py 主要提交.patch \
    --output-dir ./test_env \
    --source-dir .
```

### 场景4: 生成变更报告

```bash
python3 backend/scripts/generate_files_from_patch.py 主要提交.patch \
    --summary change_report.txt \
    --list-only
```

## 注意事项

1. **文件内容**: `generate_files_from_patch.py` 会尝试提取新增文件的完整内容，但修改文件只显示变更片段
2. **目录创建**: 使用 `--create-dirs` 或 `--create-files` 时会自动创建所需的目录结构
3. **编码**: 工具默认使用 UTF-8 编码，如果patch文件使用其他编码可能会出现问题
4. **大文件**: 对于非常大的patch文件，处理可能需要一些时间

## 高级用法

### 批量处理多个patch文件

```bash
for patch in *.patch; do
    echo "处理: $patch"
    python3 backend/scripts/extract_files_from_patch.py "$patch" \
        --output-file "${patch%.patch}_files.txt"
done
```

### 生成完整的文件结构用于部署

```bash
# 创建部署目录
mkdir -p deployment

# 从项目复制完整文件
python3 backend/scripts/generate_files_from_patch.py 主要提交.patch \
    --output-dir deployment \
    --source-dir . \
    --summary deployment/summary.txt
```

## 故障排除

### 问题1: 无法解析patch文件

**原因**: patch文件格式不正确或编码问题

**解决**: 
- 检查patch文件是否是标准的git patch格式
- 尝试使用 `--encoding` 参数指定编码（如果工具支持）

### 问题2: 文件路径错误

**原因**: patch文件中的路径格式不标准

**解决**: 
- 检查patch文件中的路径格式
- 手动编辑patch文件修正路径

### 问题3: 权限错误

**原因**: 没有写入权限

**解决**: 
```bash
chmod +x backend/scripts/*.py
chmod -R 755 ./output_dir
```

## 相关命令

### 从Git生成patch文件

```bash
# 生成单个提交的patch
git format-patch -1 <commit_hash>

# 生成多个提交的patch
git format-patch <start_commit>..<end_commit>

# 生成当前未提交的变更的patch
git diff > changes.patch
```

### 应用patch文件

```bash
# 应用patch（需要先创建文件结构）
git apply 主要提交.patch

# 检查patch是否可以应用（不实际应用）
git apply --check 主要提交.patch
```

