# SQLBot 批量化测试脚本

## 简介

本脚本用于对SQLBot系统进行自动化批量测试，支持多问题并发测试，输出详细的Excel测试报告。

## 系统要求

- Python 3.10+
- uv 虚拟环境
- SQLBot后端服务正常运行

## 快速开始

### 1. 安装依赖

```bash
cd scripts/batch_test
pip install -r requirements.txt
```

### 2. 创建示例问题列表

```bash
python create_sample_questions.py
```

### 3. 配置参数

编辑 `config.json` 文件：

```json
{
  "api_base_url": "http://localhost:8000/api/v1",
  "user_token": "your_token_here",
  "questions_file": "questions.xlsx",
  "output_file": "test_report.xlsx",
  "requests_per_minute": 3,
  "max_concurrent": 5,
  "timeout_seconds": 300,
  "log_file": "batch_test.log"
}
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| api_base_url | SQLBot API地址 | http://localhost:8000/api/v1 |
| user_token | 用户认证token | (必填) |
| questions_file | 测试问题列表文件 | questions.xlsx |
| output_file | 输出报告文件 | test_report.xlsx |
| requests_per_minute | 每分钟最大请求数 | 3 |
| max_concurrent | 最大并发数 | 5 |
| timeout_seconds | 请求超时时间(秒) | 300 |
| log_file | 日志文件 | batch_test.log |

### 并发建议

根据系统分析，建议配置：
- **requests_per_minute**: 3-5（每分钟发送的问题数）
- **max_concurrent**: 5-10（最大并发数）

系统当前配置：
- ThreadPoolExecutor max_workers=200
- PostgreSQL连接池: PG_POOL_SIZE=20, PG_MAX_OVERFLOW=30

数据库连接是主要瓶颈，建议从低速率开始测试。

### 4. 运行测试

```bash
python main.py
```

### 5. 清除进度（重新开始）

```bash
python main.py --clear-progress
```

## 文件结构

```
batch_test/
├── api_client.py          # API客户端模块
├── config.json            # 配置文件
├── create_sample_questions.py  # 创建示例问题列表
├── main.py                # 主入口
├── questions.xlsx         # 测试问题列表
├── rate_limiter.py        # 速率限制模块
├── README.md              # 使用说明
├── report_generator.py    # 报告生成模块
├── requirements.txt       # 依赖列表
├── trace_collector.py     # 执行轨迹收集模块
└── test_report.xlsx       # 输出报告（运行后生成）
```

## 输入文件格式

### questions.xlsx

| 字段名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| question_id | int | 是 | 问题编号，从1开始 |
| question | str | 是 | 用户问题文本 |
| expected_ds_id | int | 否 | 预期数据源ID |
| tags | str | 否 | 标签，用于分类 |

## 输出报告字段

| 字段名 | 说明 |
|--------|------|
| question_id | 问题编号 |
| question | 用户问题 |
| expected_ds_id | 预期数据源ID |
| chat_id | 创建的对话ID |
| record_id | 对话记录ID |
| actual_ds_id | 实际数据源ID |
| sql_generated | 生成的SQL |
| sql_status | SQL状态 |
| sql_error | SQL错误信息 |
| row_count | 返回行数 |
| chart_type | 图表类型 |
| analysis_text | 分析文本 |
| step_xxx_ms | 各步骤耗时(毫秒) |
| total_duration_ms | 总耗时 |
| success | 是否成功 |
| error_message | 错误信息 |

## 报告统计汇总

报告包含"统计汇总"sheet，包含：
- 总问题数
- 成功/失败数
- 成功率
- 各步骤平均耗时

## 断点续测

脚本支持断点续测功能：
- 每次完成一个问题后自动保存进度
- 重新运行时自动跳过已完成的问题
- 使用 `--clear-progress` 参数清除进度重新开始