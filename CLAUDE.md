# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

SQLBot 是一个智能问数助手，可将自然语言问题转换为 SQL 查询并生成可视化图表。采用 FastAPI 后端 + Vue 3 前端的全栈架构。

## 开发命令

### 后端 (Python/FastAPI)

```bash
cd backend

# 安装依赖（推荐使用 uv）
uv pip install -e .

# 安装 GPU 支持版本
uv pip install -e ".[cu128]"  # CUDA 12.8
uv pip install -e ".[cpu]"    # CPU 版本

# 启动开发服务器
python main.py
# 或使用 uvicorn：
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 数据库迁移
alembic upgrade head              # 应用迁移
alembic downgrade -1              # 回退一个版本
alembic revision --autogenerate -m "描述"  # 创建新迁移

# 代码格式化与检查
bash scripts/format.sh            # 使用 ruff 格式化
bash scripts/lint.sh              # 使用 mypy 和 ruff 检查

# 运行测试
bash scripts/test.sh              # 运行 pytest 并生成覆盖率报告
```

### 前端 (Vue 3/TypeScript)

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev

# 构建生产版本
npm run build

# 预览生产构建
npm run preview

# 代码检查
npm run lint
```

## 架构说明

### 后端目录结构

```
backend/
├── main.py              # FastAPI 入口，中间件配置，生命周期管理
├── apps/                # 业务模块（FastAPI 路由）
│   ├── api.py           # 路由聚合
│   ├── chat/            # 核心：自然语言 → SQL 转换
│   │   ├── context/     # 多轮对话上下文处理、问题增强
│   │   ├── task/        # SQL 生成的 LLM 编排
│   │   └── curd/        # CRUD 操作
│   ├── datasource/      # 数据库连接管理
│   ├── terminology/     # 业务术语与同义词
│   ├── data_training/   # SQL 示例库（用于训练）
│   ├── template/        # SQL 模板生成与匹配
│   ├── system/          # 认证、用户、AI 模型配置、助手管理
│   └── mcp/             # MCP 服务集成
├── common/
│   ├── core/            # 配置(settings)、缓存、响应中间件
│   └── utils/           # Embedding 工具、日志
├── alembic/             # 数据库迁移版本
└── template.yaml        # SQL生成、分析、图表的 LLM 提示词模板
```

### 核心架构模式

1. **配置管理**: 通过 `common/core/config.py` 使用 pydantic-settings。环境变量文件位于项目根目录 `.env`。

2. **LLM 集成**: 使用 LangChain/LangGraph。提示词定义在 `backend/template.yaml`。支持多种 LLM API（OpenAI 兼容格式、DashScope/通义千问）。

3. **Embedding 嵌入**: 两种模式：
   - 本地模式: HuggingFace `shibing624/text2vec-base-chinese` (768维)
   - API模式: Qwen3-embedding-8B (4096维)，通过 `EMBEDDING_API_BASE_URL` 配置

4. **多轮对话流程**: `apps/chat/context/` 负责：
   - 问题增强 (`question_enhance_llm.py`)
   - 上下文提取 (`extractors.py`)
   - 提示词构建 (`prompt_builder.py`)

5. **SQL 生成流水线** (`apps/chat/task/llm.py`): 核心类 `LLMService` 编排完整流程：

   **主流程** (`run_task`):
   1. **问题增强**: 多轮对话时，基于历史日志补全指代词（如"这些公司"）→ `ContextStateManager`
   2. **术语检索**: 基于 embedding 检索业务术语同义词
   3. **训练数据检索**: 检索相似 SQL 示例模板（快速模板匹配数据源）
   4. **数据源选择**: 若无数据源，使用 LLM 选择合适的数据源
   5. **表结构获取** (`_resolve_db_schema_for_sql`): 
      - 若启用 `TABLE_SELECTOR_LLM_ENABLED`，先由 LLM 选择相关表，再获取指定表 schema
      - 否则使用 embedding 检索相关表结构
   6. **快速模板匹配** (`generate_straight_sql_info`):
      - 基于训练数据匹配用户问题与模板问题相似度
      - **二次校验** (`double_check_straight_sql_info`): 校验实体类型、范围限定词（海外/境内）、并表/存续口径、明细/汇总一致性
      - **隐式参数替换**: 将模板硬编码实体（如"广东省"）替换为用户问句实体（如"四川省"）
   7. **标准 SQL 生成** (`generate_sql`): 使用 LLM 生成 JSON 格式响应（含 sql/tables/chart-type）
   8. **SQL 校验与自动修复** (`_validate_and_autofix_sql_answer`):
      - 基础语法检查（括号匹配、引号闭合）
      - 数据库原生语法校验（EXPLAIN/EXPLAIN SYNTAX/PARSEONLY）
      - 自动修复：LLM 基于错误信息修复，或规则化兜底修复（如 ClickHouse 中文别名加引号）
   9. **图表生成** (`generate_chart`): 基于 SQL 生成图表配置（table/column/bar/line/pie）
   10. **数据分析** (`generate_analysis`): 生成数据洞察报告（可选启用反思修正节点）

   **关键设计**:
   - 支持流式输出 (SSE)，每个步骤可实时推送前端
   - 执行链路追踪 (`_trace_start/end`)，记录完整调用链
   - 结构化输出约束 (`response_format=json_schema`)，失败自动回退普通模式

6. **数据库支持**: PostgreSQL（主要），以及 MySQL、SQL Server、Oracle、ClickHouse、Doris、达梦、Redshift 等。

### 前端目录结构

```
frontend/src/
├── views/               # 页面组件
├── components/          # 可复用 UI 组件
├── api/                 # 后端 API 调用 (axios)
├── stores/              # Pinia 状态管理
├── router/              # Vue Router 配置
└── i18n/                # 国际化 (vue-i18n)
```

## 关键配置开关

`config.py` 中影响系统行为的重要配置：

- `LEGACY_DOMAIN_RULES_ENABLED`: 新数据库建议设为 `False`，避免历史硬编码业务规则干扰
- `TABLE_EMBEDDING_ENABLED`: 启用基于 embedding 的表选择
- `SQL_AUTOFIX_ENABLED`: SQL 语法错误自动修复
- `ANALYSIS_REFLECTION_ENABLED`: 分析报告的事实核查与修正
- `QUESTION_ENHANCE_API_URL`: 多轮对话问题补全的 LLM API 地址

## 提示词模板

`backend/template.yaml` 包含所有 LLM 提示词：
- `sql.system`/`sql.user`: SQL 生成主模板
- `template.straight`/`template.double_check`: 模板匹配与校验
- `chart.system`/`chart.user`: 图表配置生成
- `analysis.system`/`analysis.user`: 数据分析报告
- `guess.system`: 猜你想问/推荐问题
- `question_enhance`: 多轮对话上下文处理

## 测试

测试文件位于项目根目录 `tests/`（当前基本为空）。后端测试使用 pytest + coverage，配置见 `pyproject.toml`。