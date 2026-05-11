# SQLBot 批量化测试脚本 - 实现计划

## [x] Task 1: 创建项目结构与依赖配置
- **Priority**: P0
- **Depends On**: None
- **Description**: 
  - 创建测试脚本目录结构
  - 配置依赖（pandas, openpyxl, requests）
  - 创建示例问题列表和配置文件
- **Acceptance Criteria Addressed**: AC-1
- **Test Requirements**:
  - `programmatic` TR-1.1: 目录结构正确创建（batch_test/目录包含所有必要文件）
  - `programmatic` TR-1.2: requirements.txt包含所有依赖包
- **Notes**: 需要在uv虚拟环境中安装依赖

## [ ] Task 2: 实现API客户端模块
- **Priority**: P0
- **Depends On**: Task 1
- **Description**: 
  - 实现HTTP客户端封装
  - 处理流式响应解析
  - 实现重试机制和超时控制
- **Acceptance Criteria Addressed**: AC-3
- **Test Requirements**:
  - `programmatic` TR-2.1: 成功调用/chat/start接口创建对话
  - `programmatic` TR-2.2: 成功调用/chat/question接口发送问题并解析流式响应
- **Notes**: 注意处理Server-Sent Events(SSE)格式

## [ ] Task 3: 实现并发控制模块
- **Priority**: P0
- **Depends On**: Task 2
- **Description**: 
  - 实现基于时间窗口的速率限制
  - 实现异步任务队列
  - 支持配置requests_per_minute参数
- **Acceptance Criteria Addressed**: AC-2
- **Test Requirements**:
  - `programmatic` TR-3.1: 配置每分钟3个请求时，1分钟内不超过3个请求
  - `programmatic` TR-3.2: 支持动态调整速率参数
- **Notes**: 建议使用asyncio + semaphore实现

## [ ] Task 4: 实现执行轨迹收集
- **Priority**: P0
- **Depends On**: Task 2
- **Description**: 
  - 解析流式响应中的各个步骤
  - 记录每个步骤的耗时
  - 收集SQL、图表配置、分析文本等数据
- **Acceptance Criteria Addressed**: AC-4
- **Test Requirements**:
  - `programmatic` TR-4.1: 正确解析并记录SQL生成步骤
  - `programmatic` TR-4.2: 正确解析并记录SQL执行步骤
  - `programmatic` TR-4.3: 正确解析并记录数据分析步骤
- **Notes**: 需要解析SSE事件中的step-start和step-complete事件

## [ ] Task 5: 实现Excel报告生成
- **Priority**: P0
- **Depends On**: Task 4
- **Description**: 
  - 使用pandas生成Excel报告
  - 包含所有设计的字段
  - 支持统计汇总sheet
- **Acceptance Criteria Addressed**: AC-5
- **Test Requirements**:
  - `programmatic` TR-5.1: 生成的Excel文件包含所有设计字段
  - `human-judgment` TR-5.2: 报告格式清晰，数据完整
- **Notes**: 需要处理长文本字段（如SQL、分析文本）

## [ ] Task 6: 实现主入口与配置管理
- **Priority**: P0
- **Depends On**: Task 3, Task 5
- **Description**: 
  - 实现命令行接口
  - 解析配置文件
  - 实现断点续测功能
- **Acceptance Criteria Addressed**: AC-1, AC-2
- **Test Requirements**:
  - `programmatic` TR-6.1: 支持从命令行指定配置文件路径
  - `programmatic` TR-6.2: 断点续测正确跳过已完成的问题
- **Notes**: 使用argparse实现命令行参数解析

## [ ] Task 7: 测试与验证
- **Priority**: P1
- **Depends On**: All previous tasks
- **Description**: 
  - 编写单元测试
  - 进行集成测试
  - 优化性能和稳定性
- **Acceptance Criteria Addressed**: All
- **Test Requirements**:
  - `programmatic` TR-7.1: 单元测试覆盖率 >= 80%
  - `human-judgment` TR-7.2: 集成测试成功执行10个问题
- **Notes**: 需要测试环境正常运行

## [x] Task 8: 文档与示例
- **Priority**: P2
- **Depends On**: Task 7
- **Description**: 
  - 编写使用说明文档
  - 创建示例问题列表
  - 添加配置说明
- **Acceptance Criteria Addressed**: N/A
- **Test Requirements**:
  - `human-judgment` TR-8.1: 文档清晰易懂
  - `human-judgment` TR-8.2: 示例文件完整可用
- **Notes**: 文档放在scripts目录下