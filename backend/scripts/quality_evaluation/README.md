# SQL生成质量评测脚本

使用大模型自动评估批量测试结果中问题改写和SQL生成的质量。

## 功能特性

- ✅ 读取批量测试生成的Excel结果文件
- ✅ 使用大模型对问题改写质量进行评分
- ✅ 使用大模型对SQL生成质量进行评分
- ✅ 支持断点续评，避免重复计算
- ✅ 生成包含详细评分和统计汇总的Excel报告

## 评分维度

### 问题改写评分（0-100分）
| 维度 | 分值 | 说明 |
|------|------|------|
| 完整性 | 25分 | 改写后是否保留所有关键信息 |
| 准确性 | 25分 | 是否准确表达原始意图 |
| 丰富性 | 25分 | 是否添加必要上下文 |
| 清晰性 | 25分 | 是否更清晰易懂 |

### SQL生成评分（0-100分）
| 维度 | 分值 | 说明 |
|------|------|------|
| 语法正确性 | 25分 | SQL是否符合语法规范 |
| 逻辑正确性 | 25分 | SQL是否正确回答问题 |
| 性能优化 | 25分 | SQL是否高效 |
| 可读性 | 25分 | SQL是否易于理解维护 |

## 安装依赖

```bash
cd backend/scripts/quality_evaluation
uv pip install -r requirements.txt
```

## 配置说明

编辑 `config.json` 文件：

```json
{
  "llm": {
    "provider": "openai",
    "api_key": "your-api-key",
    "api_base": "",
    "model": "gpt-4o-mini",
    "temperature": 0.2,
    "max_tokens": 1024,
    "timeout": 60
  },
  "scoring": {
    "question_rewrite_weight": 0.4,
    "sql_generation_weight": 0.6,
    "enable_question_rewrite": true,
    "enable_sql_generation": true
  },
  "rate_limit": {
    "requests_per_minute": 30,
    "max_concurrent": 5
  },
  "output": {
    "input_file": "../batch_test/test_report.xlsx",
    "output_file": "evaluation_report.xlsx",
    "progress_file": ".evaluation_progress.json"
  }
}
```

### 配置项说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| llm.provider | 大模型提供商 | openai |
| llm.api_key | API密钥 | - |
| llm.api_base | 自定义API地址 | - |
| llm.model | 模型名称 | gpt-4o-mini |
| llm.temperature | 温度参数 | 0.2 |
| scoring.question_rewrite_weight | 问题改写权重 | 0.4 |
| scoring.sql_generation_weight | SQL生成权重 | 0.6 |
| rate_limit.requests_per_minute | 每分钟请求数 | 30 |
| output.input_file | 输入文件路径 | ../batch_test/test_report.xlsx |
| output.output_file | 输出文件路径 | evaluation_report.xlsx |

## 使用方法

### 基本使用

```bash
# 使用默认配置
uv run python main.py

# 指定输入文件
uv run python main.py -i ../batch_test/test_report.xlsx

# 指定输出文件
uv run python main.py -o my_report.xlsx

# 限制处理数量（测试用）
uv run python main.py --limit 10

# 清除进度，重新开始
uv run python main.py --clear-progress

# 抽样评测（抽取10%的数据）
uv run python main.py --sample 10

# 抽样评测（抽取5%，指定随机种子保证结果可重复）
uv run python main.py --sample 5 --sample-seed 12345

# 组合使用（抽取20%，最多处理100条）
uv run python main.py --sample 20 --limit 100
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| -c, --config | 配置文件路径 |
| -i, --input | 输入文件路径 |
| -o, --output | 输出文件路径 |
| --clear-progress | 清除进度文件 |
| --limit | 限制处理数量 |
| --sample | 抽样百分比 (0.0-100.0)，如 --sample 10 表示抽取10%的数据 |
| --sample-seed | 抽样随机种子，保证结果可重复（默认42） |
| --help | 显示帮助信息 |

### 抽样功能说明

**抽样评测**允许你从大量测试数据中抽取一部分进行评测，适用于：
- 快速评估整体质量
- 降低API调用成本
- 在有限时间内完成评测

**特性**：
- ✅ 支持按百分比抽样（0-100%）
- ✅ 支持指定随机种子，保证抽样结果可重复
- ✅ 可与 `--limit` 参数组合使用
- ✅ 抽样后自动跳过已完成的记录（断点续评）

**示例**：
```bash
# 抽取20%的数据进行评测
uv run python main.py --sample 20

# 使用不同种子获取不同抽样结果
uv run python main.py --sample 10 --sample-seed 1
uv run python main.py --sample 10 --sample-seed 2
```

## 输出报告

生成的Excel报告包含两个Sheet：

### 详细结果
包含每条记录的：
- 原始问题
- 问题改写输入/输出
- 问题改写评分和理由
- SQL生成评分和理由
- 综合评分

### 统计汇总
包含：
- 总记录数、成功数、成功率
- 各评分维度的平均分、最高分、最低分、标准差
- 评分分布统计

## 项目结构

```
quality_evaluation/
├── config.json          # 配置文件
├── requirements.txt     # 依赖列表
├── main.py              # 主入口
├── data_loader.py       # 数据加载模块
├── llm_scorer.py        # 大模型评分模块
├── report_generator.py  # 报告生成模块
└── README.md            # 使用说明
```

## 注意事项

1. 需要配置有效的大模型API密钥
2. 确保输入文件存在且格式正确
3. 首次运行会创建进度文件，中断后重新运行会自动续评
4. 建议从低速率开始测试，根据API限额调整配置