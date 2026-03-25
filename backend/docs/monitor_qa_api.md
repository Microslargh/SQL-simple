# 问数监控开放 API（Monitor QA）

供“数字员工监控平台”拉取 **Data Q&A**（问数）统计，用于大屏展示。

## 1. Base URL

默认 API 前缀：`/api/v1`（由 `API_V1_STR` 决定）

示例：
`https://your-host:9018/api/v1`

## 2. 鉴权方式

请求头：

- `X-SQLBOT-MONITOR-KEY`: 必填，需与后端配置项 `MONITOR_API_KEY` 一致

未配置 `MONITOR_API_KEY` 时：

- 返回 `503`

密钥缺失/错误时：

- 返回 `401`

以上监控接口已加入路由白名单，不需要 `X-SQLBOT-TOKEN`。

## 3. 指标口径（必须对齐）

- 统计范围：仅 `chat` 表中 `chat_type='chat'` 的会话对应 `chat_record`
- 成功（success）：`chat_record.finish = true` 且 `chat_record.error` 为空（`NULL` 或仅空白）
- 失败（failed）：区间内其余记录（含未完成、带 error 等）
- 时间字段：默认使用 `chat_record.create_time` 作为“提问时间”
- 部门字段：来自 `sys_user` 的 `orgname` / `userorg`（OAuth2 同步后的用户档案）

## 4. 公共查询参数

多数接口支持：

- `start_time`: `datetime`（ISO 8601，区间开始，含），不传则默认 `end_time` 前 30 天
- `end_time`: `datetime`（ISO 8601，区间结束，不含），不传则默认当前时间
- `oid`: `integer`（工作空间 ID），不传则全量

## 5. 接口列表

### 5.1 成功率 / 总量 / 失败量

`GET /monitor/qa/summary`

返回（data 部分）：

- `total`: int
- `success_count`: int
- `failed_count`: int
- `success_rate`: float | null
- `definition`: string

### 5.2 问数量随时间变化（趋势）

`GET /monitor/qa/timeseries?granularity=day|hour`

参数：

- `granularity`:
  - `day`: 按自然日聚合
  - `hour`: 按小时聚合

返回（data 部分）：

- `granularity`
- `points`: 数组，每项包含 `time/total/success_count/failed_count`

### 5.3 用户分布（含部门）

`GET /monitor/qa/by-user`

参数：

- `limit`（默认 500）
- `offset`（默认 0）

返回（data 部分）：

- `items[]`：
  - `user_id`: 用户 ID（`chat_record.create_by`）
  - `account`: 账号
  - `name`: 姓名
  - `userorg`: 部门编码
  - `orgname`: 部门名称
  - `query_count`: 问数次数

过滤说明：

- `by-user` 会过滤无法关联到 `sys_user` 的历史脏数据（如账号为空）
- 默认过滤系统管理员账号（`account='admin'` / `id=1`）

### 5.4 访问时间分布（一天内时段）

`GET /monitor/qa/by-hour-of-day`

推荐用法：

- 只分析某一天：传 `target_date=YYYY-MM-DD`（当日 00:00:00 到次日 00:00:00）
- 若传了 `start_time/end_time`，则按区间汇总

返回（data 部分）：

- `buckets`: 固定长度 24，包含每小时 `hour` 与 `count`

### 5.5 指标口径说明

`GET /monitor/qa/meta`

返回口径文字，便于文档化。

## 6. 使用示例（每个接口）

### 6.1 公共变量

```bash
export BASE="https://your-host:9018/api/v1"
export KEY="your-monitor-secret-key"
```

### 6.2 `summary`：成功率 / 总量 / 失败量

```bash
curl -sS -G "${BASE}/monitor/qa/summary" \
  -H "X-SQLBOT-MONITOR-KEY: ${KEY}" \
  --data-urlencode "start_time=2026-03-01T00:00:00" \
  --data-urlencode "end_time=2026-03-26T00:00:00" \
  --data-urlencode "oid=1"
```

### 6.3 `timeseries`：问数量趋势（按天/按小时）

按天：

```bash
curl -sS -G "${BASE}/monitor/qa/timeseries" \
  -H "X-SQLBOT-MONITOR-KEY: ${KEY}" \
  --data-urlencode "granularity=day" \
  --data-urlencode "start_time=2026-03-01T00:00:00" \
  --data-urlencode "end_time=2026-03-26T00:00:00"
```

按小时：

```bash
curl -sS -G "${BASE}/monitor/qa/timeseries" \
  -H "X-SQLBOT-MONITOR-KEY: ${KEY}" \
  --data-urlencode "granularity=hour" \
  --data-urlencode "start_time=2026-03-25T00:00:00" \
  --data-urlencode "end_time=2026-03-26T00:00:00"
```

### 6.4 `by-user`：用户分布（含部门）

```bash
curl -sS -G "${BASE}/monitor/qa/by-user" \
  -H "X-SQLBOT-MONITOR-KEY: ${KEY}" \
  --data-urlencode "start_time=2026-03-01T00:00:00" \
  --data-urlencode "end_time=2026-03-26T00:00:00" \
  --data-urlencode "limit=100" \
  --data-urlencode "offset=0"
```

### 6.5 `by-hour-of-day`：一天内访问时段分布

按指定某一天（推荐）：

```bash
curl -sS -G "${BASE}/monitor/qa/by-hour-of-day" \
  -H "X-SQLBOT-MONITOR-KEY: ${KEY}" \
  --data-urlencode "target_date=2026-03-25" \
  --data-urlencode "oid=1"
```

按时间区间：

```bash
curl -sS -G "${BASE}/monitor/qa/by-hour-of-day" \
  -H "X-SQLBOT-MONITOR-KEY: ${KEY}" \
  --data-urlencode "start_time=2026-03-01T00:00:00" \
  --data-urlencode "end_time=2026-03-26T00:00:00"
```

### 6.6 `meta`：口径说明

```bash
curl -sS "${BASE}/monitor/qa/meta" \
  -H "X-SQLBOT-MONITOR-KEY: ${KEY}"
```

### 6.7 PowerShell 示例

```powershell
$Base = "https://your-host:9018/api/v1"
$Key  = "your-monitor-secret-key"
$Headers = @{ "X-SQLBOT-MONITOR-KEY" = $Key }

# summary
Invoke-RestMethod -Uri "$Base/monitor/qa/summary?start_time=2026-03-01T00:00:00&end_time=2026-03-26T00:00:00&oid=1" -Headers $Headers -Method Get

# timeseries
Invoke-RestMethod -Uri "$Base/monitor/qa/timeseries?granularity=day&start_time=2026-03-01T00:00:00&end_time=2026-03-26T00:00:00" -Headers $Headers -Method Get

# by-user
Invoke-RestMethod -Uri "$Base/monitor/qa/by-user?limit=100&offset=0" -Headers $Headers -Method Get

# by-hour-of-day
Invoke-RestMethod -Uri "$Base/monitor/qa/by-hour-of-day?target_date=2026-03-25" -Headers $Headers -Method Get

# meta
Invoke-RestMethod -Uri "$Base/monitor/qa/meta" -Headers $Headers -Method Get
```

