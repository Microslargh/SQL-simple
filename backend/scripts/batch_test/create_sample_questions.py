#!/usr/bin/env python
"""创建示例问题列表Excel文件"""

import pandas as pd

def create_sample_questions(output_file='questions.xlsx'):
    """创建示例问题列表"""
    data = [
        {"question_id": 1, "question": "查询2024年各部门销售额", "expected_ds_id": 1, "tags": "简单查询"},
        {"question_id": 2, "question": "统计客户数量按地区分布", "expected_ds_id": 1, "tags": "聚合分析"},
        {"question_id": 3, "question": "显示最近一个月的订单趋势", "expected_ds_id": 1, "tags": "时间序列"},
        {"question_id": 4, "question": "找出销售额最高的前10个产品", "expected_ds_id": 1, "tags": "排序筛选"},
        {"question_id": 5, "question": "计算利润率并按部门分组", "expected_ds_id": 1, "tags": "复杂分析"},
        {"question_id": 6, "question": "查询本月新增用户数量", "expected_ds_id": 1, "tags": "简单查询"},
        {"question_id": 7, "question": "分析不同产品类别的销售占比", "expected_ds_id": 1, "tags": "比例分析"},
        {"question_id": 8, "question": "对比今年与去年的销售增长情况", "expected_ds_id": 1, "tags": "同比分析"},
        {"question_id": 9, "question": "列出库存不足的商品", "expected_ds_id": 1, "tags": "条件筛选"},
        {"question_id": 10, "question": "计算各区域的平均订单金额", "expected_ds_id": 1, "tags": "聚合分析"},
    ]
    
    df = pd.DataFrame(data)
    df.to_excel(output_file, index=False)
    print(f"示例问题列表已创建: {output_file}")

if __name__ == "__main__":
    create_sample_questions()