# SQL生成质量评测脚本 - Product Requirement Document

## Overview
- **Summary**: 创建一个基于大模型的SQL生成质量评测脚本，自动评估测试结果中每个问题的问题改写质量和SQL生成质量
- **Purpose**: 通过大模型对批量测试结果进行智能打分，量化评估系统的问题理解能力和SQL生成能力
- **Target Users**: 开发人员、测试人员、产品经理

## Goals
- [x] 读取批量测试生成的Excel结果文件
- [x] 使用大模型对问题改写模块进行打分评估
- [x] 使用大模型对SQL生成模块进行打分评估
- [x] 生成包含评分结果的Excel报告
- [x] 提供详细的评分统计和分析

## Non-Goals (Out of Scope)
- 不涉及测试脚本的运行
- 不涉及原始数据的采集
- 不修改现有测试框架
- 不提供Web界面

## Background & Context
- 已有批量测试脚本生成的结果文件 `test_report.xlsx`
- 结果文件包含：question_rewrite_input, question_rewrite_output, sql_gen_prompt, sql_generated等字段
- 需要对这些字段进行质量评估，帮助识别系统的薄弱环节

## Functional Requirements
- **FR-1**: 读取Excel格式的测试结果文件
- **FR-2**: 调用大模型API对每个问题进行评分
- **FR-3**: 评估问题改写的准确性和完整性
- **FR-4**: 评估SQL生成的正确性和优化程度
- **FR-5**: 生成包含评分结果的Excel报告
- **FR-6**: 提供评分统计汇总（平均分、最高分、最低分、分布等）

## Non-Functional Requirements
- **NFR-1**: 支持配置不同的大模型API（如OpenAI、Anthropic等）
- **NFR-2**: 支持自定义评分标准和权重
- **NFR-3**: 支持批量处理，处理速度可配置
- **NFR-4**: 支持断点续评，避免重复计算

## Constraints
- **Technical**: Python 3.10+, 需要网络连接调用大模型API
- **Business**: 需要API密钥和足够的API配额
- **Dependencies**: pandas, openai, aiohttp, python-dotenv

## Assumptions
- 测试结果文件格式符合预期（包含必要字段）
- 大模型API服务可用且响应正常
- 用户已配置好API密钥

## Acceptance Criteria

### AC-1: 读取测试结果文件
- **Given**: 存在有效的Excel测试结果文件
- **When**: 运行评测脚本并指定输入文件
- **Then**: 成功读取文件并解析所有测试记录
- **Verification**: `programmatic`
- **Notes**: 支持.xlsx格式

### AC-2: 问题改写质量评分
- **Given**: 测试记录包含question_rewrite_input和question_rewrite_output
- **When**: 调用大模型进行评估
- **Then**: 返回0-100的评分和评估理由
- **Verification**: `human-judgment`（抽样检查评分合理性）

### AC-3: SQL生成质量评分
- **Given**: 测试记录包含sql_generated和相关上下文
- **When**: 调用大模型进行评估
- **Then**: 返回0-100的评分和评估理由
- **Verification**: `human-judgment`（抽样检查评分合理性）

### AC-4: 生成评测报告
- **Given**: 所有记录评分完成
- **When**: 生成报告
- **Then**: 生成包含评分结果和统计汇总的Excel文件
- **Verification**: `programmatic`

### AC-5: 断点续评支持
- **Given**: 评测过程中断
- **When**: 重新运行脚本
- **Then**: 自动跳过已评分的记录，继续未完成的评分
- **Verification**: `programmatic`

## Open Questions
- [ ] 具体使用哪个大模型（OpenAI GPT-4, Claude 3, 还是其他）
- [ ] 是否需要支持多种评分维度（如正确性、完整性、优化程度等）
- [ ] 是否需要自定义评分提示词模板