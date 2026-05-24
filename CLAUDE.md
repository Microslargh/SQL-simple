# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

SQLBot — 自然语言转 SQL 的全栈智能问数助手。FastAPI 后端 + Vue 3 前端，支持多数据库、多轮对话、可视化图表生成。

## 开发命令

```bash
# === 后端 ===
cd backend
uv pip install -e .                  # 安装依赖
uv pip install -e ".[cu128]"         # GPU 版 (CUDA 12.8)
uv pip install -e ".[cpu]"           # CPU 版
python main.py                       # 启动开发服务器 (端口 8000)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

alembic upgrade head                 # 应用数据库迁移
alembic revision --autogenerate -m "描述"  # 创建新迁移
bash scripts/format.sh               # ruff 格式化
bash scripts/lint.sh                 # mypy + ruff 检查
bash scripts/test.sh                 # pytest + 覆盖率

# === 前端 ===
cd frontend
npm install
npm run dev                          # 启动开发服务器 (端口 5173)
npm run build                        # 生产构建
npm run lint                         # ESLint 检查
```

## 后端架构

```
backend/
├── main.py                  # FastAPI 入口，CORS/中间件/生命周期/MCP 挂载
├── apps/                    # 业务模块 (FastAPI 路由)
│   ├── chat/                # 核心：NL → SQL（context/task/curd 三子模块）
│   ├── datasource/          # 数据库连接管理（PG/MySQL/MSSQL/Oracle/ClickHouse/Doris/达梦/Redshift）
│   ├── terminology/         # 业务术语与同义词（embedding 检索）
│   ├── data_training/       # SQL 示例库训练
│   ├── template/            # SQL 模板匹配与生成
│   ├── system/              # 认证/用户/AI 模型配置/助手管理
│   ├── mcp/                 # MCP 服务集成
│   └── dashboard/           # 仪表板
├── common/
│   ├── core/                # pydantic-settings 配置、缓存、响应中间件
│   └── utils/               # Embedding 工具、日志
├── alembic/                 # 数据库迁移版本
└── template.yaml            # 所有 LLM 提示词模板
```

### SQL 生成核心管线 (`apps/chat/task/llm.py` — `LLMService.run_task`)

全程 SSE 流式推送，每步实时反馈前端；`_trace_start/end` 记录完整调用链。

1. **问题增强**: 有历史对话时，`ContextStateManager.enhance_question_with_history` 补全指代词（如"这些公司"→"广东省 XX 公司"），避免残缺问句影响后续检索
2. **术语检索**: `get_terminology_template_with_data` 基于 embedding 检索业务术语同义词，注入 prompt
3. **训练数据检索**: `get_training_template_with_data` 检索相似 SQL 示例模板 → `filter_training_data_by_entity_type` 实体类型一致性过滤（如用户问"地区"则剔除"公司"维度模板）
4. **自定义提示词加载**: 许可证有效时 `find_custom_prompts` 加载自定义提示词（`CustomPromptTypeEnum.GENERATE_SQL`）
5. **数据源选择**（ds 为空时）: `select_datasource()` LLM 自动选择合适数据源
6. **表结构获取** (`_resolve_db_schema_for_sql`): `TABLE_SELECTOR_LLM_ENABLED` 开启时 LLM 先选表再获取 schema，否则 embedding 检索；追问场景额外补全上一轮 SQL 涉及的表字段，避免漏传
7. **SQL 生成** — 两条路径:
   - **快速模板**: `generate_straight_sql_info()` 匹配模板 → `double_check_straight_sql_info()` 二次校验（实体类型/范围限定词/并表存续口径/明细汇总一致性）→ `generate_straight_sql()` 隐式参数替换（模板硬编码实体→用户问句实体，如"广东省"→"四川省"）
   - **标准生成**（快速模板未命中时）: `generate_rewrite_question()` 问题重写（未命中模板时先重写语义再生成）→ `generate_sql()` LLM JSON 结构化输出 (sql/tables/chart-type)
8. **SQL 校验与自修复** (`_validate_and_autofix_sql_answer`, `SQL_AUTOFIX_ENABLED` 控制): 基础语法检查（括号/引号闭合）→ 数据库原生校验（EXPLAIN/EXPLAIN SYNTAX/PARSEONLY）→ LLM 基于错误信息修复 → 规则化兜底（如 ClickHouse 中文别名加引号）
9. **权限过滤**: 普通用户行级权限 → `generate_filter()` 注入过滤条件；动态数据源 → `generate_assistant_dynamic_sql()` SQL 模板替换
10. **SQL 执行** (`execute_sql`): 执行最终 SQL，结果存入 `save_sql_data`
11. **图表生成** (`generate_chart`): 基于 SQL 数据 + LLM 推荐图表类型 (table/bar/line/pie) → `check_save_chart` 用实际字段自动纠偏
12. **数据分析** (`generate_analysis`): LLM 生成数据洞察报告，可选 `ANALYSIS_REFLECTION_ENABLED` 反思修正节点做事实核查

### 多轮对话上下文 (`apps/chat/context/`)

- `question_enhance_llm.py` — 问题增强
- `extractors.py` — 上下文提取
- `prompt_builder.py` — 提示词构建

### LLM 集成

LangChain/LangGraph 编排。支持 OpenAI 兼容 API、DashScope(通义千问)。提示词集中在 `template.yaml`。

### Embedding 模式

- **本地**: HuggingFace `shibing624/text2vec-base-chinese` (768维)
- **API**: Qwen3-embedding-8B (4096维)，通过 `EMBEDDING_API_BASE_URL` 切换

## 前端架构

```
frontend/src/
├── views/       # 页面组件
├── components/  # 可复用 UI 组件
├── api/         # axios 后端 API 调用
├── stores/      # Pinia 状态管理
├── router/      # Vue Router
└── i18n/        # vue-i18n 国际化
```

技术栈: Vue 3 + TypeScript + Vite + Element Plus + AntV G2/S2 + Pinia

## 关键配置开关

`common/core/config.py` 中的 `Settings` 类（环境变量文件位于 `.env`）：

| 开关 | 默认值 | 说明 |
|------|--------|------|
| `LEGACY_DOMAIN_RULES_ENABLED` | `False` | 新数据库建议关闭，避免旧硬编码业务规则干扰 |
| `TABLE_EMBEDDING_ENABLED` | `False` | 启用 embedding 表选择 |
| `TABLE_SELECTOR_LLM_ENABLED` | `True` | SQL 生成前 LLM 选表节点 |
| `SQL_AUTOFIX_ENABLED` | `True` | SQL 语法错误自动修复 |
| `ANALYSIS_REFLECTION_ENABLED` | `True` | 分析报告事实核查修正 |
| `EMBEDDING_API_BASE_URL` | `""` | 设为 API 地址则切换远程 embedding，留空使用本地模型 |
| `QUESTION_ENHANCE_API_URL` | `""` | 多轮对话 LLM 补全地址，留空仅用规则增强 |
| `CACHE_TYPE` | `"memory"` | 缓存类型: redis / memory / None |

## 提示词模板 (`template.yaml`)

- `sql.system/user` — SQL 生成主模板
- `template.straight/double_check` — 模板匹配与二次校验
- `chart.system/user` — 图表配置生成
- `analysis.system/user` — 数据分析报告
- `guess.system` — 猜你想问/推荐问题
- `question_enhance` — 多轮对话上下文处理

## 数据库支持

PostgreSQL (主要，含 pgvector) + MySQL、SQL Server、Oracle、ClickHouse、Doris、达梦、Redshift、Elasticsearch

## 测试

后端: pytest + coverage，配置见 `backend/pyproject.toml`。测试文件位于 `tests/`。
前端: ESLint + vue-tsc 类型检查。

## 编辑注意事项

### Python 文件中的 Unicode 转义序列

`Read` 工具会将文件中的 `\uXXXX` 转义序列渲染为实际 Unicode 字符（如 `一-鿿` 显示为 `一-鿿`）。但 Python 源文件里存储的是**字面的反斜杠-u 转义序列**。

**规则**：使用 `Edit` 工具编辑包含 `\uXXXX` 的 Python 代码时，old_string 中应使用 Read 工具显示的实际 Unicode 字符，而非字面的 `\uXXXX`。如果 Edit 反复失败（"String to replace not found"），改用 `python -c` 通过 Bash 工具按行号操作，避免字符集不匹配。

**已验证**：Edit 工具能够匹配 Read 渲染后的实际 Unicode 字符；两次失败通常是因为 Bash 环境打印时二次转码导致乱码，不是 Edit 本身的问题。优先信任 Edit + Read 的组合，仅在连续两次失败后回退到 Python 行号操作。
