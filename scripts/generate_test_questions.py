"""
NL2SQL 测试问题自动生成器

根据 tests/ 目录下导出的 CSV 文件，分析表结构和实际数据，
自动生成贴近业务的测试问题，输出到 tests/generated_questions.json。

使用方式:
    1. 把数据库中的表导出为 CSV 放到 tests/ 目录下（文件名即表名，如 dws_order.csv）
    2. 运行脚本:
       python scripts/generate_test_questions.py
    3. 生成的问题在 tests/generated_questions.json

可选参数:
    --csv-dir       CSV 文件目录，默认 tests/
    --output        输出文件路径，默认 tests/generated_questions.json
    --max-per-table 每张表最多生成的问题数，默认 15
    --encoding      CSV 文件编码，默认 utf-8（可用 gbk、gb2312 等）
    --sample-rows   用于分析的采样行数，默认 500
"""

import argparse
import csv
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 数据类型推断
# ---------------------------------------------------------------------------

class FieldType:
    NUMERIC = "numeric"
    TEXT = "text"
    DATE = "date"
    BOOLEAN = "boolean"
    UNKNOWN = "unknown"


def _is_numeric_value(val: str) -> bool:
    try:
        float(val.replace(",", ""))
        return True
    except (ValueError, AttributeError):
        return False


def _is_date_value(val: str) -> bool:
    date_patterns = [
        r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}",
        r"^\d{8}$",
        r"^\d{4}年\d{1,2}月",
        r"^\d{1,2}/\d{1,2}/\d{4}",
    ]
    return any(re.match(p, val.strip()) for p in date_patterns)


def _is_boolean_value(val: str) -> bool:
    return val.strip().lower() in (
        "true", "false", "yes", "no", "是", "否", "1", "0",
        "有", "无", "正常", "异常", "启用", "停用",
    )


def infer_field_type(values: list[str]) -> str:
    """根据采样值推断字段类型"""
    non_empty = [v for v in values if v and v.strip()]
    if not non_empty:
        return FieldType.UNKNOWN

    sample = non_empty[:200]
    n = len(sample)

    bool_count = sum(1 for v in sample if _is_boolean_value(v))
    if bool_count / n > 0.8:
        return FieldType.BOOLEAN

    date_count = sum(1 for v in sample if _is_date_value(v))
    if date_count / n > 0.6:
        return FieldType.DATE

    num_count = sum(1 for v in sample if _is_numeric_value(v))
    if num_count / n > 0.7:
        return FieldType.NUMERIC

    return FieldType.TEXT


# ---------------------------------------------------------------------------
# 字段分析
# ---------------------------------------------------------------------------

@dataclass
class FieldProfile:
    name: str
    display_name: str  # 用于生成问题的中文名（来自CSV表头或字段本身）
    field_type: str = FieldType.UNKNOWN
    unique_count: int = 0
    total_count: int = 0
    null_count: int = 0
    sample_values: list = field(default_factory=list)
    top_values: list = field(default_factory=list)  # [(value, count), ...]
    min_val: Optional[str] = None
    max_val: Optional[str] = None
    is_id_like: bool = False
    is_high_cardinality: bool = False


@dataclass
class TableProfile:
    file_name: str
    table_name: str
    display_name: str
    row_count: int = 0
    fields: list = field(default_factory=list)

    @property
    def numeric_fields(self) -> list[FieldProfile]:
        return [f for f in self.fields if f.field_type == FieldType.NUMERIC and not f.is_id_like]

    @property
    def text_fields(self) -> list[FieldProfile]:
        return [f for f in self.fields if f.field_type == FieldType.TEXT and not f.is_id_like and not f.is_high_cardinality]

    @property
    def date_fields(self) -> list[FieldProfile]:
        return [f for f in self.fields if f.field_type == FieldType.DATE]

    @property
    def boolean_fields(self) -> list[FieldProfile]:
        return [f for f in self.fields if f.field_type == FieldType.BOOLEAN]

    @property
    def group_fields(self) -> list[FieldProfile]:
        """适合做 GROUP BY 的字段：低基数文本 / 布尔"""
        result = []
        for f in self.fields:
            if f.is_id_like or f.is_high_cardinality:
                continue
            if f.field_type == FieldType.TEXT and 2 <= f.unique_count <= 50:
                result.append(f)
            elif f.field_type == FieldType.BOOLEAN:
                result.append(f)
        return result


def _guess_display_name(col_name: str) -> str:
    """尝试从列名推测中文展示名"""
    has_chinese = any("\u4e00" <= c <= "\u9fff" for c in col_name)
    if has_chinese:
        return col_name
    return col_name


def _is_id_column(col_name: str, values: list[str]) -> bool:
    """判断是否为 ID 列"""
    name_lower = col_name.lower().strip()
    if name_lower in ("id", "pk", "key") or name_lower.endswith("_id") or name_lower.endswith("_pk"):
        return True
    if name_lower.startswith("id_"):
        return True
    non_empty = [v for v in values if v and v.strip()]
    if len(non_empty) > 10:
        unique_ratio = len(set(non_empty)) / len(non_empty)
        if unique_ratio > 0.95 and all(_is_numeric_value(v) for v in non_empty[:50]):
            return True
    return False


def profile_table(csv_path: str, sample_rows: int = 500, encoding: str = "utf-8") -> Optional[TableProfile]:
    """分析一个 CSV 文件，返回表画像"""
    file_name = os.path.basename(csv_path)
    table_name = os.path.splitext(file_name)[0]

    try:
        with open(csv_path, "r", encoding=encoding, errors="replace") as f:
            reader = csv.reader(f)
            headers = next(reader, None)
            if not headers:
                return None

            columns_data: dict[str, list[str]] = {h: [] for h in headers}
            row_count = 0
            for row in reader:
                row_count += 1
                for i, h in enumerate(headers):
                    val = row[i].strip() if i < len(row) else ""
                    columns_data[h].append(val)
                if row_count >= sample_rows:
                    break
    except Exception as e:
        print(f"  [警告] 读取 {csv_path} 失败: {e}", file=sys.stderr)
        return None

    profile = TableProfile(
        file_name=file_name,
        table_name=table_name,
        display_name=table_name,
        row_count=row_count,
    )

    for col_name in headers:
        values = columns_data[col_name]
        non_empty = [v for v in values if v and v.strip()]

        fp = FieldProfile(
            name=col_name,
            display_name=_guess_display_name(col_name),
            field_type=infer_field_type(values),
            unique_count=len(set(non_empty)),
            total_count=len(values),
            null_count=len(values) - len(non_empty),
            is_id_like=_is_id_column(col_name, values),
        )

        if non_empty:
            fp.sample_values = list(set(non_empty))[:10]
            counter = Counter(non_empty)
            fp.top_values = counter.most_common(10)

            if fp.field_type == FieldType.NUMERIC:
                try:
                    nums = [float(v.replace(",", "")) for v in non_empty if _is_numeric_value(v)]
                    if nums:
                        fp.min_val = str(min(nums))
                        fp.max_val = str(max(nums))
                except Exception:
                    pass
            elif fp.field_type == FieldType.DATE:
                sorted_dates = sorted(non_empty)
                fp.min_val = sorted_dates[0]
                fp.max_val = sorted_dates[-1]

        if fp.total_count > 0:
            fp.is_high_cardinality = fp.unique_count / max(fp.total_count, 1) > 0.8 and fp.unique_count > 50

        profile.fields.append(fp)

    return profile


# ---------------------------------------------------------------------------
# 问题生成引擎
# ---------------------------------------------------------------------------

class QuestionEngine:
    """基于表画像生成测试问题"""

    def __init__(self, table: TableProfile):
        self.t = table
        self.questions: list[dict] = []

    def _add(self, question: str, category: str, difficulty: str = "easy",
             target_fields: list[str] = None):
        self.questions.append({
            "question": question,
            "category": category,
            "difficulty": difficulty,
            "table": self.t.table_name,
            "target_fields": target_fields or [],
        })

    def generate_all(self) -> list[dict]:
        self._count_questions()
        self._list_questions()
        self._aggregate_questions()
        self._filter_questions()
        self._topn_questions()
        self._group_questions()
        self._trend_questions()
        self._comparison_questions()
        self._ratio_questions()
        self._combined_questions()
        return self.questions

    # -- 计数类 ---------------------------------------------------------------
    def _count_questions(self):
        tn = self.t.display_name
        self._add(f"一共有多少条{tn}的数据？", "count")
        self._add(f"{tn}的总记录数是多少？", "count")

        for bf in self.t.boolean_fields[:2]:
            for val, cnt in bf.top_values[:2]:
                self._add(
                    f"{tn}中{bf.display_name}为「{val}」的有多少条？",
                    "count_filter", "easy", [bf.name],
                )

    # -- 列表类 ---------------------------------------------------------------
    def _list_questions(self):
        tn = self.t.display_name
        self._add(f"查看{tn}的前10条数据", "list")

        for df in self.t.date_fields[:1]:
            self._add(
                f"查看最近的{tn}数据",
                "list_sort", "easy", [df.name],
            )

    # -- 聚合类 ---------------------------------------------------------------
    def _aggregate_questions(self):
        tn = self.t.display_name
        for nf in self.t.numeric_fields[:3]:
            fn = nf.display_name
            self._add(f"{tn}的{fn}总计是多少？", "aggregate_sum", "easy", [nf.name])
            self._add(f"{tn}的{fn}平均值是多少？", "aggregate_avg", "easy", [nf.name])
            self._add(f"{tn}中{fn}的最大值和最小值分别是多少？", "aggregate_minmax", "easy", [nf.name])

    # -- 筛选类 ---------------------------------------------------------------
    def _filter_questions(self):
        tn = self.t.display_name
        for tf in self.t.text_fields[:3]:
            if not tf.top_values:
                continue
            val = tf.top_values[0][0]
            if len(val) > 20:
                continue
            self._add(
                f"查询{tf.display_name}为「{val}」的所有{tn}",
                "filter", "easy", [tf.name],
            )

        for df in self.t.date_fields[:1]:
            if df.max_val:
                year_match = re.match(r"(\d{4})", df.max_val)
                if year_match:
                    year = year_match.group(1)
                    self._add(
                        f"查询{year}年的{tn}数据",
                        "filter_date", "medium", [df.name],
                    )

    # -- TopN 类 --------------------------------------------------------------
    def _topn_questions(self):
        tn = self.t.display_name
        for nf in self.t.numeric_fields[:2]:
            fn = nf.display_name
            self._add(
                f"{tn}中{fn}最高的前5条记录",
                "top_n", "medium", [nf.name],
            )
            self._add(
                f"{tn}中{fn}最低的前5条记录",
                "bottom_n", "medium", [nf.name],
            )

    # -- 分组统计 --------------------------------------------------------------
    def _group_questions(self):
        tn = self.t.display_name
        for gf in self.t.group_fields[:3]:
            gn = gf.display_name
            self._add(
                f"按{gn}统计{tn}的数量",
                "group_count", "medium", [gf.name],
            )
            for nf in self.t.numeric_fields[:1]:
                fn = nf.display_name
                self._add(
                    f"按{gn}统计{fn}的总和",
                    "group_sum", "medium", [gf.name, nf.name],
                )
                self._add(
                    f"按{gn}统计{fn}的平均值",
                    "group_avg", "medium", [gf.name, nf.name],
                )

    # -- 趋势类 ---------------------------------------------------------------
    def _trend_questions(self):
        tn = self.t.display_name
        for df in self.t.date_fields[:1]:
            dn = df.display_name
            self._add(
                f"{tn}按{dn}的数量变化趋势",
                "trend", "medium", [df.name],
            )
            for nf in self.t.numeric_fields[:1]:
                fn = nf.display_name
                self._add(
                    f"{tn}的{fn}按月变化趋势如何？",
                    "trend_metric", "medium", [df.name, nf.name],
                )

    # -- 对比类 ---------------------------------------------------------------
    def _comparison_questions(self):
        tn = self.t.display_name
        for gf in self.t.group_fields[:2]:
            gn = gf.display_name
            if len(gf.top_values) >= 2:
                v1, v2 = gf.top_values[0][0], gf.top_values[1][0]
                if len(v1) <= 15 and len(v2) <= 15:
                    self._add(
                        f"对比{gn}为「{v1}」和「{v2}」的{tn}数量差异",
                        "comparison", "medium", [gf.name],
                    )
            for nf in self.t.numeric_fields[:1]:
                fn = nf.display_name
                self._add(
                    f"不同{gn}的{fn}对比情况",
                    "comparison_metric", "medium", [gf.name, nf.name],
                )

    # -- 占比类 ---------------------------------------------------------------
    def _ratio_questions(self):
        tn = self.t.display_name
        for gf in self.t.group_fields[:2]:
            gn = gf.display_name
            self._add(
                f"各{gn}在{tn}中的占比分布",
                "ratio", "hard", [gf.name],
            )

    # -- 组合查询 --------------------------------------------------------------
    def _combined_questions(self):
        tn = self.t.display_name
        gfs = self.t.group_fields
        nfs = self.t.numeric_fields
        dfs = self.t.date_fields

        if gfs and nfs and dfs:
            gf, nf, df = gfs[0], nfs[0], dfs[0]
            if df.max_val:
                year_match = re.match(r"(\d{4})", df.max_val)
                if year_match:
                    year = year_match.group(1)
                    self._add(
                        f"{year}年各{gf.display_name}的{nf.display_name}汇总",
                        "combined", "hard", [gf.name, nf.name, df.name],
                    )

        if len(gfs) >= 2 and nfs:
            g1, g2, nf = gfs[0], gfs[1], nfs[0]
            self._add(
                f"按{g1.display_name}和{g2.display_name}统计{nf.display_name}的分布",
                "combined_multi_group", "hard", [g1.name, g2.name, nf.name],
            )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def scan_csv_files(csv_dir: str) -> list[str]:
    """扫描目录下所有 CSV 文件"""
    csv_files = []
    for f in sorted(os.listdir(csv_dir)):
        if f.lower().endswith(".csv"):
            csv_files.append(os.path.join(csv_dir, f))
    return csv_files


def main():
    parser = argparse.ArgumentParser(description="根据 CSV 数据自动生成 NL2SQL 测试问题")
    parser.add_argument("--csv-dir", type=str, default="tests",
                        help="CSV 文件目录（默认 tests/）")
    parser.add_argument("--output", type=str, default="tests/generated_questions.json",
                        help="输出文件路径（默认 tests/generated_questions.json）")
    parser.add_argument("--max-per-table", type=int, default=15,
                        help="每张表最多生成的问题数（默认 15）")
    parser.add_argument("--encoding", type=str, default="utf-8",
                        help="CSV 文件编码（默认 utf-8，可用 gbk、gb2312）")
    parser.add_argument("--sample-rows", type=int, default=500,
                        help="每个 CSV 采样的行数（默认 500）")
    parser.add_argument("--show-profile", action="store_true",
                        help="打印每张表的字段分析详情")

    args = parser.parse_args()

    csv_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), args.csv_dir) \
        if not os.path.isabs(args.csv_dir) else args.csv_dir
    output_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), args.output) \
        if not os.path.isabs(args.output) else args.output

    print(f"扫描 CSV 目录: {csv_dir}")
    csv_files = scan_csv_files(csv_dir)

    if not csv_files:
        print(f"\n[错误] 在 {csv_dir} 下没有找到 CSV 文件。")
        print("请先将数据库表导出为 CSV 文件放到该目录。")
        sys.exit(1)

    print(f"找到 {len(csv_files)} 个 CSV 文件\n")

    all_questions = []
    table_profiles = []

    for csv_path in csv_files:
        file_name = os.path.basename(csv_path)
        print(f"分析: {file_name} ...", end=" ")

        profile = profile_table(csv_path, sample_rows=args.sample_rows, encoding=args.encoding)
        if not profile:
            print("跳过（无法解析）")
            continue

        print(f"{profile.row_count} 行, {len(profile.fields)} 列")
        table_profiles.append(profile)

        if args.show_profile:
            print(f"  字段分析:")
            for fp in profile.fields:
                id_tag = " [ID]" if fp.is_id_like else ""
                hc_tag = " [高基数]" if fp.is_high_cardinality else ""
                top_vals = ", ".join(f"{v}({c})" for v, c in fp.top_values[:3])
                print(f"    {fp.name}: {fp.field_type}{id_tag}{hc_tag}"
                      f" | 唯一值={fp.unique_count} 空值={fp.null_count}"
                      f" | 高频: {top_vals}")

        engine = QuestionEngine(profile)
        questions = engine.generate_all()

        if len(questions) > args.max_per_table:
            easy = [q for q in questions if q["difficulty"] == "easy"]
            medium = [q for q in questions if q["difficulty"] == "medium"]
            hard = [q for q in questions if q["difficulty"] == "hard"]

            selected = []
            for pool in [easy, medium, hard]:
                quota = max(1, int(args.max_per_table * len(pool) / max(len(questions), 1)))
                selected.extend(pool[:quota])
            questions = selected[:args.max_per_table]

        all_questions.extend(questions)
        print(f"  -> 生成 {len(questions)} 个测试问题")

    print(f"\n{'='*50}")
    print(f"共生成 {len(all_questions)} 个测试问题")

    # 按难度统计
    difficulty_count = Counter(q["difficulty"] for q in all_questions)
    category_count = Counter(q["category"] for q in all_questions)
    print(f"\n按难度分布:")
    for d in ["easy", "medium", "hard"]:
        print(f"  {d}: {difficulty_count.get(d, 0)}")
    print(f"\n按类型分布:")
    for cat, cnt in sorted(category_count.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {cnt}")

    # 保存
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_questions, f, ensure_ascii=False, indent=2)
    print(f"\n测试问题已保存到: {output_path}")

    # 同时输出一份纯问题列表（方便快速查看）
    txt_path = output_path.replace(".json", ".txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        for i, q in enumerate(all_questions, 1):
            f.write(f"{i}. [{q['difficulty']}][{q['category']}] {q['question']}\n")
    print(f"问题清单已保存到: {txt_path}")

    # 输出表画像摘要
    summary_path = output_path.replace(".json", "_schema_summary.json")
    summary = []
    for tp in table_profiles:
        summary.append({
            "table_name": tp.table_name,
            "file_name": tp.file_name,
            "row_count": tp.row_count,
            "fields": [
                {
                    "name": fp.name,
                    "type": fp.field_type,
                    "unique_count": fp.unique_count,
                    "null_count": fp.null_count,
                    "is_id": fp.is_id_like,
                    "sample_values": fp.sample_values[:5],
                    "top_values": [{"value": v, "count": c} for v, c in fp.top_values[:5]],
                }
                for fp in tp.fields
            ],
        })
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"表结构摘要已保存到: {summary_path}")


if __name__ == "__main__":
    main()
