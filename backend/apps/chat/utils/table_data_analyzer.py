"""
表格数据智能预分析模块

在将数据传给 LLM 做数据分析前，由代码预计算关键统计量（总数、分项之和等），
避免模型因长表注意力漂移导致遗漏或计算错误。

适用场景：
1. 分类+数值 结构（如各产业户数、各板块营收等）- 已聚合
2. 明细表按分类 结构（如每行一家公司，含产业名称列）- 需按分类列聚合计数
3. 纯明细表（公司名称|属性1|属性2|属性3）- 无数值列，按属性聚合或提供样本
4. 数值编码的分类列：属性用数字表示（如境内境外 0/1，与国家出资企业关系 1全资2参股3控股4等）
5. 字符串类型的分类列：属性用字符串表示（如境内境外「境内」「境外」，全资参股控股「全资」「参股」「控股」等）
"""
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple


def _try_float(val: Any) -> Optional[float]:
    """尝试将值转为浮点数，失败返回 None"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        s = str(val).strip().replace(",", "").replace(" ", "")
        if not s:
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def _is_numeric_column(data: List[Dict], key: str, min_numeric_ratio: float = 0.8) -> bool:
    """判断列是否为数值列（大部分值可解析为数字）"""
    if not data or not key:
        return False
    numeric_count = 0
    for row in data:
        v = row.get(key)
        if _try_float(v) is not None:
            numeric_count += 1
    return numeric_count >= len(data) * min_numeric_ratio


def _get_column_keys(data: List[Dict]) -> List[str]:
    """从数据中提取列名（取第一行 keys，保证顺序）"""
    if not data:
        return []
    first = data[0]
    if isinstance(first, dict):
        return list(first.keys())
    return []


def compute_table_summary(
    raw_data: List[Dict[str, Any]],
    field_names: Optional[List[str]] = None,
    chart_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    对表格数据做智能预分析，计算统计量供 LLM 使用。

    Args:
        raw_data: 原始数据，每行为一个 dict
        field_names: 列名列表（可选，不传则从数据推断）
        chart_config: 图表配置（可选，用于识别分类列/数值列）

    Returns:
        {
            "row_count": int,
            "column_sums": {col: sum},       # 数值列之和
            "breakdown_text": str,          # 逐行明细（紧凑文本，供 LLM 使用）
            "breakdown_rows": [...],        # 逐行明细（结构化）
            "has_numeric_breakdown": bool,  # 是否为「分类+数值」结构
        }
    """
    result: Dict[str, Any] = {
        "row_count": 0,
        "column_sums": {},
        "breakdown_text": "",
        "breakdown_rows": [],
        "has_numeric_breakdown": False,
    }

    if not raw_data:
        return result

    keys = field_names or _get_column_keys(raw_data)
    if not keys:
        return result

    result["row_count"] = len(raw_data)

    # 识别数值列：优先从图表配置取，再自动检测
    numeric_cols: List[str] = []
    if chart_config:
        # 饼图/柱状图：axis.y 为数值列
        if chart_config.get("axis"):
            axis = chart_config["axis"]
            y_col = axis.get("y")
            if y_col and isinstance(y_col, dict):
                v = y_col.get("value") or y_col.get("name")
                if v and v in keys and _is_numeric_column(raw_data, v):
                    numeric_cols.append(v)
            if not numeric_cols:
                for t in ["x", "series"]:
                    col = axis.get(t)
                    if col and isinstance(col, dict):
                        v = col.get("value") or col.get("name")
                        if v and v in keys and _is_numeric_column(raw_data, v):
                            numeric_cols.append(v)
        # 表格类型：从 columns 中检测数值列
        if not numeric_cols and chart_config.get("columns"):
            for col in chart_config["columns"]:
                if isinstance(col, dict):
                    v = col.get("value") or col.get("name")
                    if v and v in keys and _is_numeric_column(raw_data, v):
                        numeric_cols.append(v)
        numeric_cols = list(dict.fromkeys(numeric_cols))

    if not numeric_cols:
        for k in keys:
            if _is_numeric_column(raw_data, k):
                numeric_cols.append(k)

    # 检测「伪数值列」：当数值列之和=行数时，可能是每行填1的明细表，应走聚合而非逐行
    use_aggregation_instead = False
    if numeric_cols and len(raw_data) > 50:
        col_sum = sum(_try_float(row.get(numeric_cols[0])) or 0 for row in raw_data)
        if abs(col_sum - len(raw_data)) < len(raw_data) * 0.05:
            category_col = _detect_category_column_for_aggregation(raw_data, keys, chart_config)
            if not category_col:
                for k in keys:
                    if k in numeric_cols:
                        continue
                    uniq = len(set(str(row.get(k, "")).strip() for row in raw_data if row.get(k)))
                    if 2 <= uniq <= 50:
                        category_col = k
                        break
            if category_col:
                use_aggregation_instead = True

    if numeric_cols and not use_aggregation_instead:
        result["has_numeric_breakdown"] = True
        category_cols = [k for k in keys if k not in numeric_cols]
        for col in numeric_cols:
            total = 0.0
            for row in raw_data:
                v = _try_float(row.get(col))
                if v is not None:
                    total += v
            result["column_sums"][col] = int(total) if total == int(total) else round(total, 2)

        MAX_BREAKDOWN_ROWS = 100
        MAX_TEXT_LINES = 30  # text is redundant with data JSON; keep small
        total_rows = len(raw_data)
        sample_data = raw_data[:MAX_BREAKDOWN_ROWS]
        lines = []
        text_sample = sample_data[:MAX_TEXT_LINES]
        for i, row in enumerate(text_sample, 1):
            cat_parts = [str(row.get(k, "")) for k in category_cols]
            cat_str = " | ".join(cat_parts) if cat_parts else "-"
            num_parts = [f"{col}={row.get(col)}" for col in numeric_cols]
            num_str = ", ".join(num_parts)
            lines.append(f"[{i}] {cat_str}: {num_str}")
        if total_rows > MAX_BREAKDOWN_ROWS:
            lines.append(f"... (共 {total_rows} 行，以上仅展示前 {MAX_TEXT_LINES} 行文本样本，JSON数据含前 {MAX_BREAKDOWN_ROWS} 行)")

        # When sampling, also compute full category distributions so LLM sees
        # accurate breakdowns (e.g. 境外=250, 境内=780) instead of sample-biased counts
        if total_rows > MAX_BREAKDOWN_ROWS:
            cat_dist_lines = _build_category_distributions(raw_data, keys, numeric_cols)
            if cat_dist_lines:
                lines.append("")
                lines.append("【全量分类分布统计（非抽样，基于全部数据）】")
                lines.extend(cat_dist_lines)

        result["breakdown_rows"] = sample_data
        result["breakdown_text"] = "\n".join(lines)
        result["row_count"] = total_rows
        if total_rows > MAX_BREAKDOWN_ROWS:
            result["is_sampled"] = True
        return result

    # 无数值列 或 伪数值列（明细表）：按分类列聚合
    category_col = _detect_category_column_for_aggregation(raw_data, keys, chart_config)
    if not category_col and len(raw_data) > 50:
        for k in keys:
            if _is_numeric_column(raw_data, k):
                continue
            uniq = len(set(str(row.get(k, "")).strip() for row in raw_data if row.get(k)))
            if 2 <= uniq <= 50:
                category_col = k
                break
    if category_col:
        agg_result = _aggregate_by_category(raw_data, category_col)
        if agg_result:
            result["has_numeric_breakdown"] = True
            result["column_sums"] = {"户数": agg_result["total"]}
            result["breakdown_rows"] = agg_result["rows"]
            result["category_col"] = category_col
            lines = []
            for i, (cat_name, count) in enumerate(agg_result["rows"], 1):
                lines.append(f"[{i}] {cat_name}: 户数={count}")
            result["breakdown_text"] = "\n".join(lines)
            result["row_count"] = len(agg_result["rows"])
    else:
        # 无数值列且无合适分类列（如纯明细表 |公司名称|属性1|属性2|）：提供总行数+样本
        if len(raw_data) > 50:
            result["has_numeric_breakdown"] = True
            result["column_sums"] = {}
            result["breakdown_rows"] = raw_data[:100]
            result["breakdown_text"] = f"共 {len(raw_data)} 条明细，列: {', '.join(keys)}"
            result["is_detail_only"] = True

    return result


def _build_category_distributions(
    raw_data: List[Dict], keys: List[str], numeric_cols: List[str]
) -> List[str]:
    """Build full category distribution stats from ALL rows (not sampled).

    Finds low-cardinality categorical columns (2-50 unique values) and returns
    exact count distributions so the LLM can report accurate numbers even when
    the detail sample is truncated.
    """
    if not raw_data or not keys:
        return []
    lines: List[str] = []
    candidate_cols = [k for k in keys if k not in numeric_cols]
    for col in candidate_cols:
        counter: Counter[str] = Counter()
        for row in raw_data:
            v = row.get(col)
            if v is not None:
                s = str(v).strip()
                if s:
                    counter[s] += 1
        uniq = len(counter)
        if 2 <= uniq <= 50:
            total = sum(counter.values())
            items = counter.most_common()
            dist_str = ", ".join(f"{name}={cnt}" for name, cnt in items)
            lines.append(f"{col}: {dist_str} (总计 {total})")
    return lines


def _count_unique(data: List[Dict], key: str) -> int:
    """统计某列不同值数量"""
    if not data or not key:
        return 0
    vals = [str(row.get(key, "")).strip() for row in data if row.get(key) is not None]
    return len(set(v for v in vals if v))


def _detect_category_column_for_aggregation(
    data: List[Dict], keys: List[str], chart_config: Optional[Dict]
) -> Optional[str]:
    """检测适合按聚合计数的分类列（如产业名称、板块等），含数值编码（0/1、1/2/3/4）及字符串分类（境内境外、全资参股控股等）"""
    if not data or not keys:
        return None
    # 从图表配置取分类列时，必须校验唯一值数量：避免 axis.x=公司名称 导致 1099 个分类
    MAX_CATEGORY_FROM_CHART = 50
    if chart_config:
        if chart_config.get("axis"):
            for t in ["series", "x"]:
                col = chart_config["axis"].get(t)
                if col and isinstance(col, dict):
                    v = col.get("value") or col.get("name")
                    if v and v in keys:
                        uniq = _count_unique(data, v)
                        if uniq <= MAX_CATEGORY_FROM_CHART:
                            if not _is_numeric_column(data, v) or (2 <= uniq <= 50):
                                return v
        if chart_config.get("columns"):
            for col in chart_config["columns"]:
                if isinstance(col, dict):
                    v = col.get("value") or col.get("name")
                    if v and v in keys:
                        uniq = _count_unique(data, v)
                        if uniq <= MAX_CATEGORY_FROM_CHART:
                            if not _is_numeric_column(data, v) or (2 <= uniq <= 50):
                                return v
    # 自动检测：非数值列 + 数值编码分类列（0/1、1/2/3/4）+ 字符串分类列（境内境外、全资参股控股等）
    category_keywords = ("产业", "板块", "类型", "分类", "名称", "产业名", "属性", "关系","标识", "境内", "境外", "出资", "全资", "参股", "控股", "plate", "bk", "org", "type", "category")
    exclude_keywords = ("公司名", "company", "id", "code")  # 公司名、id 等通常为明细维度，排除高基数列
    candidates: List[Tuple[str, int, int]] = []  # (col, uniq_count, priority)
    for k in keys:
        vals = [str(row.get(k, "")).strip() for row in data if row.get(k) is not None]
        uniq = len(set(v for v in vals if v))
        is_numeric = _is_numeric_column(data, k)
        if is_numeric and not (2 <= uniq <= 50):
            continue  # 真数值列（如户数、营收），非编码分类，跳过
        k_lower = k.lower()
        if uniq > 100 and len(data) > 100 and any(ex in k or ex in k_lower for ex in exclude_keywords):
            continue
        if 2 <= uniq <= 500:
            priority = 0 if any(kw in k or kw in k_lower for kw in category_keywords) else 1
            if is_numeric:
                priority = 0  # 数值编码分类（0/1、1/2/3/4）优先
            candidates.append((k, uniq, priority))
    if candidates:
        candidates.sort(key=lambda x: (x[2], x[1]))
        return candidates[0][0]
    return None


def _aggregate_by_category(data: List[Dict], category_col: str) -> Optional[Dict[str, Any]]:
    """按分类列聚合计数，返回 {total, rows: [(cat_name, count), ...]}"""
    if not data or not category_col:
        return None
    counter: Counter = Counter()
    for row in data:
        v = row.get(category_col)
        if v is not None:
            s = str(v).strip()
            if s:
                counter[s] += 1
    if not counter:
        return None
    total = sum(counter.values())
    rows = sorted(counter.items(), key=lambda x: -x[1])
    return {"total": total, "rows": rows}


def build_detail_sample(
    raw_data: List[Dict],
    category_col: str,
    breakdown_rows: List[Tuple[str, int]],
    name_cols: Optional[List[str]] = None,
    max_per_category: int = 4,
    max_total: int = 120,
) -> Tuple[List[Dict], str]:
    """
    按分类分层抽样，生成明细样本供模型深入分析（可引用企业名等）。
    Returns: (sample_rows, detail_sample_text)
    """
    if not raw_data or not category_col or not breakdown_rows:
        return [], ""
    name_cols = name_cols or ["公司名称", "公司名", "企业名称", "名称", "name", "company"]
    by_cat: Dict[str, List[Dict]] = defaultdict(list)
    for row in raw_data:
        cat = str(row.get(category_col, "")).strip()
        if cat:
            by_cat[cat].append(row)
    sample: List[Dict] = []
    lines: List[str] = []
    for cat_name, _ in breakdown_rows:
        rows = by_cat.get(cat_name, [])
        taken = rows[:max_per_category]
        sample.extend(taken)
        if taken:
            names = []
            for r in taken:
                for nc in name_cols:
                    v = r.get(nc)
                    if v and str(v).strip():
                        names.append(str(v).strip())
                        break
            if names:
                lines.append(f"{cat_name}: {', '.join(names)}")
    if len(sample) > max_total:
        sample = sample[:max_total]
    detail_text = "\n".join(lines) if lines else ""
    return sample, detail_text


def format_summary_for_prompt(
    summary: Dict[str, Any],
    detail_sample_text: str = "",
) -> str:
    """
    将预分析结果格式化为可注入 prompt 的文本。
    detail_sample_text: 各分类下的代表企业/样本，供模型深入分析时引用。
    """
    if not summary or summary.get("row_count", 0) == 0:
        return ""

    parts: List[str] = ["【系统预计算-不可篡改】"]
    if not summary.get("is_detail_only"):
        parts.append("【以下为全部分类，无遗漏；禁止编造「其他」「未分类」等数据中不存在的分类】")
    parts.append(f"总行数: {summary['row_count']}")

    if summary.get("column_sums"):
        sums_str = ", ".join(f"{k}={v}" for k, v in summary["column_sums"].items())
        parts.append(f"数值列合计: {sums_str}")

    if summary.get("breakdown_text"):
        if summary.get("is_detail_only"):
            parts.append(summary["breakdown_text"])
        else:
            parts.append("分项明细（逐行，仅可列举以下分类，不得添加其他）:")
            parts.append(summary["breakdown_text"])
            if summary.get("column_sums"):
                total = sum(summary["column_sums"].values())
                parts.append(f"分项之和 = {total} ✓")

    if detail_sample_text:
        parts.append("")
        parts.append("【各分类代表样本（供深入分析时引用企业名、属性等）】")
        parts.append(detail_sample_text)

    return "\n".join(parts)
