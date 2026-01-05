# Patch文件处理工具快速指南

## 工具列表

1. **extract_files_from_patch.py** - 提取文件列表和目录结构
2. **generate_files_from_patch.py** - 根据patch文件生成完整的文件结构

## 快速开始

### 1. 查看patch文件涉及的文件

```bash
# 基本用法
python3 backend/scripts/extract_files_from_patch.py 主要提交.patch

# 分别显示新增和修改的文件，并生成文件树
python3 backend/scripts/extract_files_from_patch.py 主要提交.patch --separate --tree
```

### 2. 生成文件结构（从本地项目复制完整文件）

```bash
# 从本地项目复制完整文件到目标目录（推荐）
python3 backend/scripts/generate_files_from_patch.py 登录优化.patch \
    --output-dir ./extracted \
    --source-dir .

# 指定源项目目录
python3 backend/scripts/generate_files_from_patch.py 移动端登出方法兼容.patch\
    --output-dir ./extracted \
    --source-dir /path/to/project
```

### 3. 生成摘要报告

```bash
python3 backend/scripts/generate_files_from_patch.py 主要提交.patch \
    --summary report.txt \
    --list-only
```

## 输出示例

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
```

## 支持的Patch格式

- ✅ 标准Git patch格式 (`diff --git`)
- ✅ IntelliJ IDEA patch格式 (`Index:`)

## 完整文档

详细使用说明请参考: `backend/scripts/PATCH_TO_FILES_GUIDE.md`

