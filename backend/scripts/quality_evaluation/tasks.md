# SQL生成质量评测脚本 - 实现计划

## [x] Task 1: 创建项目结构和配置文件
- **Priority**: P0
- **Depends On**: None
- **Description**: 
  - 创建quality_evaluation目录结构
  - 创建config.json配置文件（大模型API配置、评分参数等）
  - 创建requirements.txt依赖文件
- **Acceptance Criteria Addressed**: [AC-1, AC-2, AC-3]
- **Test Requirements**:
  - `programmatic` TR-1.1: 配置文件能够正确加载
  - `human-judgement` TR-1.2: 目录结构清晰，配置项完整
- **Notes**: 配置文件应包含API密钥、模型选择、评分标准等

## [x] Task 2: 实现Excel文件读取模块
- **Priority**: P0
- **Depends On**: Task 1
- **Description**: 
  - 使用pandas读取Excel测试结果文件
  - 解析必要字段：question_rewrite_input, question_rewrite_output, sql_generated等
  - 处理空值和异常数据
- **Acceptance Criteria Addressed**: [AC-1]
- **Test Requirements**:
  - `programmatic` TR-2.1: 成功读取包含1000+条记录的Excel文件
  - `programmatic` TR-2.2: 正确处理空值字段
- **Notes**: 需要处理不同版本Excel格式

## [x] Task 3: 实现大模型评分模块
- **Priority**: P0
- **Depends On**: Task 1
- **Description**: 
  - 实现大模型API客户端（支持OpenAI/Claude）
  - 构建评分提示词模板
  - 解析大模型返回的评分结果
- **Acceptance Criteria Addressed**: [AC-2, AC-3]
- **Test Requirements**:
  - `programmatic` TR-3.1: 成功调用大模型API并获取评分
  - `programmatic` TR-3.2: 正确解析0-100分的评分结果
  - `human-judgement` TR-3.3: 提示词设计合理，评分结果可信
- **Notes**: 需要处理API调用失败和超时

## [x] Task 4: 实现问题改写质量评分逻辑
- **Priority**: P0
- **Depends On**: Task 2, Task 3
- **Description**: 
  - 提取question_rewrite_input和question_rewrite_output
  - 构建问题改写评分提示词
  - 调用大模型进行评分
- **Acceptance Criteria Addressed**: [AC-2]
- **Test Requirements**:
  - `programmatic` TR-4.1: 对每条记录生成问题改写评分
  - `human-judgement` TR-4.2: 评分结果与人工评估一致（抽样检查）
- **Notes**: 评分维度包括：完整性、准确性、丰富性

## [x] Task 5: 实现SQL生成质量评分逻辑
- **Priority**: P0
- **Depends On**: Task 2, Task 3
- **Description**: 
  - 提取sql_generated和相关上下文
  - 构建SQL评分提示词（正确性、优化程度、可读性）
  - 调用大模型进行评分
- **Acceptance Criteria Addressed**: [AC-3]
- **Test Requirements**:
  - `programmatic` TR-5.1: 对每条记录生成SQL评分
  - `human-judgement` TR-5.2: 评分结果与人工评估一致（抽样检查）
- **Notes**: 评分维度包括：语法正确性、逻辑正确性、性能优化、可读性

## [x] Task 6: 实现断点续评机制
- **Priority**: P1
- **Depends On**: Task 2, Task 4, Task 5
- **Description**: 
  - 使用进度文件记录已评分的记录ID
  - 运行时检查进度文件，跳过已评分记录
  - 定期保存进度
- **Acceptance Criteria Addressed**: [AC-5]
- **Test Requirements**:
  - `programmatic` TR-6.1: 中断后重新运行能正确跳过已完成记录
  - `programmatic` TR-6.2: 进度文件正确保存和读取
- **Notes**: 进度文件使用JSON格式

## [x] Task 7: 实现报告生成模块
- **Priority**: P0
- **Depends On**: Task 2, Task 4, Task 5
- **Description**: 
  - 将评分结果写入Excel文件
  - 生成统计汇总（平均分、最高分、最低分、分布统计）
  - 支持多个sheet（详细结果、统计汇总）
- **Acceptance Criteria Addressed**: [AC-4]
- **Test Requirements**:
  - `programmatic` TR-7.1: 成功生成包含评分结果的Excel文件
  - `programmatic` TR-7.2: 统计汇总数据准确
- **Notes**: 使用openpyxl支持.xlsx格式

## [x] Task 8: 实现主入口和命令行接口
- **Priority**: P0
- **Depends On**: 所有其他任务
- **Description**: 
  - 创建main.py主入口脚本
  - 支持命令行参数（输入文件、输出文件、配置文件等）
  - 显示进度条和日志信息
- **Acceptance Criteria Addressed**: [AC-1, AC-4]
- **Test Requirements**:
  - `programmatic` TR-8.1: 正确解析命令行参数
  - `human-judgement` TR-8.2: 进度显示清晰，日志信息有用
- **Notes**: 使用argparse处理命令行参数

## [x] Task 9: 编写README文档
- **Priority**: P2
- **Depends On**: 所有其他任务
- **Description**: 
  - 编写使用说明文档
  - 说明配置项含义
  - 提供使用示例
- **Acceptance Criteria Addressed**: 文档完整性
- **Test Requirements**:
  - `human-judgement` TR-9.1: 文档清晰易懂
  - `human-judgement` TR-9.2: 示例命令可正确运行
- **Notes**: README应包含配置说明、使用方法、示例输出

## [x] Task 10: 测试和优化
- **Priority**: P1
- **Depends On**: 所有其他任务
- **Description**: 
  - 使用示例数据测试评分流程
  - 优化API调用性能（并发调用、重试机制）
  - 修复发现的bug
- **Acceptance Criteria Addressed**: 所有AC
- **Test Requirements**:
  - `programmatic` TR-10.1: 测试用例全部通过
  - `human-judgement` TR-10.2: 评分结果合理可信
- **Notes**: 需要准备测试用数据