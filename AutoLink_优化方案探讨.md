# 基于 AutoLink 论文的 SQLBot 优化方案探讨

## 一、E:\cursor (SQLBot) 项目当前实现路径与方法

### 1.1 项目定位与技术栈

- **项目**：SQLBot — 智能问数小助手，自然语言 → SQL → 图表。
- **后端**：FastAPI、SQLModel、LangChain、OpenAI、pgvector、多数据源（PostgreSQL / MySQL / SQL Server / Oracle / Excel 等）。
- **核心链路**：用户问题 → 获取表结构（schema）→ 拼进 Prompt → LLM 生成 SQL → 执行与展示。

### 1.2 当前 Schema 获取流程（关键路径）

**入口**：`apps/chat/task/llm.py` 中创建 `LLMService` 时或生成 SQL 前，调用：

```text
chat_question.db_schema = get_table_schema(
    session, current_user, ds, question, embedding=True
)
```

**实现位置**：`apps/datasource/crud/datasource.py` 的 `get_table_schema()`。

**具体步骤**：

| 步骤 | 实现方式 | 说明 |
|------|----------|------|
| 1. 取全量表列表 | `get_table_obj_by_ds()` | 按数据源 + 权限拿到所有表及字段（含表备注、字段备注） |
| 2. 表级 Schema 文本 | `_build_schema_table_str()` | 每个表拼成一段文本：`# Table: xxx [ (field:type, comment), ... ]` |
| 3. 表级 Embedding 筛选 | `get_table_embedding()`（若 `TABLE_EMBEDDING_ENABLED`） | 用 **整表 schema 文本** 做文档向量，与 **用户问题** 做余弦相似度，按相似度排序后取前 `TABLE_EMBEDDING_COUNT`（默认 10）个表 |
| 4. 关系补全 | `ds.table_relation` | 根据配置的表关系（外键/边），把与已选表有关联但被裁掉的表补回来 |
| 5. 输出 | 拼接成一大段 `schema_str` | 作为 `db_schema` 填入 SQL 生成的 system/user 模板 |

**Embedding 相关**：

- **表级**：`apps/datasource/embedding/table_embedding.py` — 对「整表」的 schema 文本做 `embed_documents`，再与问题的 `embed_query` 算相似度，取 Top-K 表。
- **数据源级**：`apps/datasource/embedding/ds_embedding.py` — 多数据源场景下，用「数据源名+描述+整库 schema」做 embedding 选数据源。

**SQL 生成**：

- `chat_question.db_schema` 通过 `get_sql_template()` 的 system/user 模板填入（`apps/chat/models/chat_model.py` 中 `sql_sys_question()` / `sql_user_question()`）。
- 追问时：从上一轮 SQL 解析出表名，用 `get_table_schema_for_tables()` 补全这些表的字段定义，拼到 `db_schema` 后面（`llm.py` 约 1781–1808 行）。

### 1.3 当前方案的局限（与 AutoLink 要解决的问题对应）

| 问题 | 当前 SQLBot 表现 | AutoLink 对应思路 |
|------|------------------|-------------------|
| **Schema 粒度** | **表级**：要么整表进 prompt，要么整表丢弃；无法按「列」精细召回 | **列级文档 + 列级检索**，只召回与问题相关的列，减少噪声、提高召回 |
| **单轮检索** | 一次表级 embedding 排序后固定，缺表/缺列无法在「同一次请求」内补救 | **多轮 Agent**：初始 Top-K 列 + 迭代调用 schema_retrieval / sql_execution / sql_draft 逐步扩展 |
| **大库扩展性** | 表多时：要么只给 10 张表（易漏），要么关掉 embedding 全量给（超长、噪声大） | **不喂全库**：仅用列级检索 + Agent 探索，在 3000+ 列场景仍能保持高召回与可控 token |
| **漏列 / 多表同名列** | 依赖表关系补全和追问补表，对「关键列在未选中表」「*id/*name 等通用列名」不敏感 | **显式提示 + 工具**：Agent 关注 *id/*name/*type，用 sql_execution 查列名、抽样值，用 schema_retrieval 按列补全 |
| **Schema 是否够用** | 没有「先草拟 SQL 再检查」的闭环 | **sql_draft**：限制次数地草拟 SQL，执行后若不满足再继续 schema_retrieval / sql_execution |
| **上下文与成本** | 大 schema 一次性进 prompt，占满上下文、成本高 | **渐进式**：只把「当前轮链接到的 schema」放进 prompt，按需扩展 |

---

## 二、AutoLink 方法在 SQLBot 中的可借鉴点（优化方向）

### 2.1 列级文档与列级检索（替代/补充表级）

- **现状**：SQLBot 是「表 → 整表 schema 文本 → 表级向量」。
- **借鉴**：AutoLink 为每个 **列** 建文档（表名、列名、类型、值样例、描述），再做向量检索。
- **优化建议**：
  - 在现有 `CoreTable` / `CoreField` 基础上，为每个数据源维护 **列级文档**（可异步生成、落库或缓存），内容包含：`table_name`、`field_name`、`field_type`、`custom_comment`、可选「列值样例」。
  - 新增「列级 embedding」模块：对列文档做 `embed_documents`，建 FAISS/ pgvector 索引（按 ds_id 分索引）。
  - `get_table_schema()` 的 **第一段** 改为：用「用户问题 + 可选术语」做 query，做列级 Top-K（如 50–100 列），再按表聚合、去重表，生成「初始 schema 子集」，而不是先表级 Top-K 再整表给。

这样在「表多但每表只用少量列」的场景下，能显著减少 token、提高相关列召回率。

### 2.2 初始检索 + 多轮 Agent 探索（Schema Linking 阶段）

- **现状**：一次 `get_table_schema()` 定终身，没有「发现不够再补」的回合。
- **借鉴**：AutoLink 将 schema linking 做成 **多轮 Agent**：每轮可调用 `@schema_retrieval`、`@sql_execution`、`@sql_draft`、`@stop()`，最多 10 轮，直到 Agent 认为 schema 已够用。
- **优化建议**：
  - 将「取 schema」拆成两个阶段：  
    - **阶段一（Schema Linking）**：在真正生成「最终 SQL」之前，先跑一个 **Schema Linking Agent**（可同模型、也可轻量模型）：  
      - 输入：用户问题 + 初始列级检索得到的 schema 子集 + 可选术语/权限说明。  
      - 工具：  
        - `schema_retrieval(table, column, description)`：基于列级索引再检若干列（可复用上面列级检索），把结果追加到「已链接 schema」。  
        - `sql_execution(query)`：只读、LIMIT 小（如 5），用于查某表列名、抽样行、某列取值，便于理解语义和多表同名列。  
        - `sql_draft(query)`：最多 1–2 次，草拟一条回答问题的 SQL，验证当前 schema 是否足够。  
        - `stop()`：结束 schema linking，输出「最终 linked schema」。  
      - 输出：供第二阶段使用的 **完整 linked schema 文本**（及可选结构化列/表列表）。
    - **阶段二（SQL 生成）**：与现有流程一致，用 **linked schema** 作为 `db_schema` 填入现有 SQL 模板，由现有 LLM 生成最终 SQL。
  - 实现上：可新增 `apps/datasource/schema_linking/`（或 `apps/chat/schema_linking/`），内含 Agent 的 prompt、工具封装、与列级检索/DB 执行的对接；`get_table_schema()` 改为「列级初始检索 + 可选 Schema Linking Agent」，再返回最终 schema 字符串，这样对现有 `llm.py` 和模板改动最小。

### 2.3 工具与约束设计（直接借鉴 AutoLink）

- **schema_retrieval**：  
  - 入参：table、column、description（可选）。  
  - 实现：调用列级检索（按 table/column/description 拼 query），返回 Top-K 列，并 **排除已在当前 linked schema 中的列**，避免重复。  
  - 与现有权限对接：只从当前用户有权限的表/列中检索（可继续用 `get_table_obj_by_ds` 的权限结果做白名单）。

- **sql_execution**：  
  - 只读、短查询、LIMIT 小；可复用现有 `exec_sql`，但需严格限制为 SELECT + LIMIT，避免写操作。  
  - 用于：查 `INFORMATION_SCHEMA`/表结构、抽样行、某列取值，便于 Agent 理解「多表同名列」「*id/*name」等。

- **sql_draft**：  
  - 限制每轮 1 次或总共 2 次，避免无限试 SQL；执行结果返回给 Agent，用于判断是否缺表/缺列，再决定是否继续 schema_retrieval。

- **多轮纪律**：  
  - 在 Schema Linking 的 system prompt 中明确：禁止假设工具结果，必须「调用工具 → 等待返回 → 仅根据真实结果再推理/再调用」，与 AutoLink 一致，减少幻觉。

### 2.4 与现有组件的衔接

- **数据源与权限**：继续用 `get_table_obj_by_ds()`、行级/列级权限；列级文档只对有权限的表/列构建；Agent 内 `sql_execution` 走现有 `exec_sql`，自然带上权限。
- **术语与训练**：术语、data_training、custom_prompt 可在 Schema Linking 的 user 输入里一并传入，让 Agent 在「补全 schema」时就知道业务用语和约束。
- **表关系**：保留现有 `table_relation` 补全逻辑，可在「列级初始检索 + Agent 结束后」再跑一遍：若 linked schema 中已出现某表，则把与其有关系的表补全进来（只补表结构），与现有 554–599 行逻辑兼容。
- **追问**：追问时仍可保留「从上一轮 SQL 解析表名 + get_table_schema_for_tables 补表」；若已引入 Schema Linking，可改为「上一轮 linked schema + 本轮问题」再跑一次轻量 Schema Linking（或仅列级检索），避免重复全量 Agent。

### 2.5 配置与开关

- 建议增加配置项，例如：  
  - `SCHEMA_LINKING_ENABLED`：是否启用「列级检索 + Schema Linking Agent」。  
  - `SCHEMA_LINKING_MAX_TURNS`：最大回合数（默认 5–10）。  
  - `COLUMN_EMBEDDING_TOP_K`：列级初始检索 Top-K。  
- 当 `SCHEMA_LINKING_ENABLED=False` 时，保持现有「表级 embedding + 关系补全」逻辑不变，便于渐进式上线与回滚。

---

## 三、实施优先级建议

| 优先级 | 内容 | 说明 |
|--------|------|------|
| P0 | 列级文档 + 列级检索，替代/补充表级 | 收益大、与现有表结构兼容，可先做「列级 Top-K → 按表聚合」作为新分支，与现有表级分支做 A/B 或配置切换 |
| P1 | Schema Linking Agent（多轮 + 三工具） | 在列级检索基础上加 Agent，显著提升大库与复杂问题的召回与可扩展性 |
| P2 | sql_draft + 探索性 sql_execution 的 prompt 与约束 | 与 P1 一起设计，避免过度调用、控制成本 |
| P3 | 表关系在「linked schema 之后」再补全 | 小改现有 relation 逻辑，在 Agent 输出上再跑一遍补全 |

---

## 四、小结

- **SQLBot 当前**：表级 schema、表级 embedding、单次检索、关系补全、追问时按历史表补表；没有按列精细召回，也没有「先探索再定 schema」的闭环。
- **AutoLink 核心**：列级文档与检索 + 多轮 Agent（schema_retrieval / sql_execution / sql_draft / stop）+ 不喂全库、渐进扩展，适合工业级大 schema。
- **优化落点**：在 E:\cursor 中引入「列级文档与列级检索」和「Schema Linking Agent」两阶段，保留现有权限、术语、表关系与 SQL 生成模板，用配置开关做渐进式优化，可在不大改现有对话与 SQL 生成流程的前提下，提升大库下的召回率、控制 token 与噪声，并改善对 *id/*name 等关键列与多表同名列的处理。
