# SQLBot 批量化测试脚本 - 产品需求文档

## Overview
- **Summary**: 开发一个批量化测试脚本，用于对SQLBot系统进行自动化测试，支持多问题并发测试，输出详细的Excel测试报告。
- **Purpose**: 通过自动化测试评估SQLBot的SQL生成准确性、执行成功率、数据分析质量等关键指标。
- **Target Users**: 测试工程师、开发人员、产品经理

## Goals
- 支持批量问题测试，每个问题独立创建对话窗口
- 控制并发请求速率，避免系统过载
- 输出详细的Excel测试报告，包含完整的执行轨迹

## Non-Goals (Out of Scope)
- 不涉及前端UI测试
- 不进行性能压测（仅功能验证）
- 不修改核心业务逻辑

## Background & Context
- 系统当前配置：
  - ThreadPoolExecutor max_workers=200
  - PostgreSQL连接池：PG_POOL_SIZE=20, PG_MAX_OVERFLOW=30
  - 数据库连接是主要瓶颈
- 根据代码分析，建议并发速率：**每分钟3-5个问题**（考虑到LLM调用和数据库操作的耗时）

## Functional Requirements
- **FR-1**: 支持从Excel/CSV导入测试问题列表
- **FR-2**: 每个问题独立创建对话（调用/chat/start接口）
- **FR-3**: 支持配置并发参数（每分钟发送数量）
- **FR-4**: 收集完整的执行轨迹（SQL生成、执行、图表生成、分析）
- **FR-5**: 输出Excel报告，包含关键指标

## Non-Functional Requirements
- **NFR-1**: 测试过程不影响线上业务（低速率控制）
- **NFR-2**: 支持断点续测（记录已完成的问题）
- **NFR-3**: 报告生成时间 < 测试总耗时的10%

## Constraints
- **Technical**: Python 3.10+, FastAPI, pandas, openpyxl
- **Business**: 需要有效的用户token进行API调用
- **Dependencies**: SQLBot后端服务正常运行

## Assumptions
- 后端服务已启动并正常运行
- 测试用户已创建且有有效数据源权限
- 测试问题列表格式正确

## Acceptance Criteria

### AC-1: 问题列表导入
- **Given**: 用户提供Excel格式的测试问题列表（包含问题ID、问题文本、预期数据源ID）
- **When**: 脚本启动并加载问题列表
- **Then**: 脚本成功解析所有问题并显示问题总数
- **Verification**: `programmatic`

### AC-2: 并发控制
- **Given**: 配置每分钟发送3个问题
- **When**: 执行批量测试
- **Then**: 脚本按配置速率发送请求，不超过系统承载能力
- **Verification**: `programmatic`

### AC-3: 独立对话窗口
- **Given**: 有多个测试问题
- **When**: 执行测试
- **Then**: 每个问题创建独立的chat会话，会话之间相互独立
- **Verification**: `programmatic`

### AC-4: 执行轨迹记录
- **Given**: 问题执行完成
- **When**: 收集执行结果
- **Then**: 记录SQL生成、执行耗时、成功/失败状态、分析文本等信息
- **Verification**: `programmatic`

### AC-5: Excel报告生成
- **Given**: 所有测试完成
- **When**: 生成报告
- **Then**: 输出Excel文件，包含完整的测试结果统计
- **Verification**: `human-judgment`

## Open Questions
- [ ] 需要确认测试用户的token获取方式
- [ ] 需要确认数据源ID配置

---

# 测试报告字段设计

| 字段名 | 数据类型 | 说明 | 来源 |
|--------|----------|------|------|
| question_id | int | 问题编号 | 输入文件 |
| question | str | 用户问题文本 | 输入文件 |
| expected_ds_id | int | 预期数据源ID | 输入文件 |
| chat_id | int | 创建的对话ID | /chat/start响应 |
| record_id | int | 对话记录ID | /chat/question响应 |
| actual_ds_id | int | 实际使用的数据源ID | 执行轨迹 |
| sql_generated | str | 生成的SQL语句 | record.sql_answer |
| sql_status | str | SQL状态(success/failed) | 执行结果 |
| sql_error | str | SQL错误信息 | 错误响应 |
| row_count | int | 查询返回行数 | 执行结果 |
| chart_type | str | 图表类型 | record.chart |
| analysis_text | str | 分析文本 | record.analysis_answer |
| step_question_enhance_ms | int | 问题增强耗时(ms) | 执行轨迹 |
| step_terminology_ms | int | 术语检索耗时(ms) | 执行轨迹 |
| step_training_ms | int | 训练数据检索耗时(ms) | 执行轨迹 |
| step_sql_gen_ms | int | SQL生成耗时(ms) | 执行轨迹 |
| step_sql_exec_ms | int | SQL执行耗时(ms) | 执行轨迹 |
| step_chart_ms | int | 图表生成耗时(ms) | 执行轨迹 |
| step_analysis_ms | int | 数据分析耗时(ms) | 执行轨迹 |
| total_duration_ms | int | 总耗时(ms) | 计算值 |
| success | bool | 是否成功 | 综合判断 |
| error_message | str | 错误信息 | 错误响应 |

---

# 输入文件格式

## 问题列表文件 (questions.xlsx)

| 字段名 | 数据类型 | 必填 | 说明 |
|--------|----------|------|------|
| question_id | int | 是 | 唯一标识，从1开始 |
| question | str | 是 | 用户问题文本 |
| expected_ds_id | int | 否 | 预期使用的数据源ID |
| tags | str | 否 | 标签，用于分类（如：简单查询/复杂分析） |

## 配置文件 (config.json)

```json
{
  "api_base_url": "http://localhost:8000/api/v1",
  "user_token": "your_token_here",
  "questions_file": "questions.xlsx",
  "output_file": "test_report.xlsx",
  "requests_per_minute": 3,
  "max_concurrent": 5,
  "timeout_seconds": 300
}
```