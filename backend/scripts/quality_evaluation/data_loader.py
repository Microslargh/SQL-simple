#!/usr/bin/env python
"""数据加载模块"""

import json
import logging
import os
import random
from typing import List, Dict, Any

import pandas as pd

logger = logging.getLogger(__name__)

class DataLoader:
    """数据加载器"""

    def __init__(self, input_file: str):
        self.input_file = input_file
        self.data: pd.DataFrame = None

    def load_data(self) -> pd.DataFrame:
        """加载Excel数据"""
        if not os.path.exists(self.input_file):
            raise FileNotFoundError(f"输入文件不存在: {self.input_file}")

        try:
            self.data = pd.read_excel(self.input_file, engine="openpyxl")
            logger.info(f"成功加载 {len(self.data)} 条记录")
            return self.data
        except Exception as e:
            logger.error(f"加载文件失败: {e}")
            raise

    def get_records(self) -> List[Dict[str, Any]]:
        """获取记录列表"""
        if self.data is None:
            self.load_data()
        
        records = []
        for _, row in self.data.iterrows():
            record = row.to_dict()
            # 处理空值
            for key, value in record.items():
                if pd.isna(value):
                    record[key] = ""
            records.append(record)
        
        return records

    def get_sampled_records(self, sample_percent: float, random_seed: int = 42) -> List[Dict[str, Any]]:
        """
        获取抽样记录
        
        Args:
            sample_percent: 抽样百分比 (0.0-100.0)
            random_seed: 随机种子，保证抽样结果可重复
        
        Returns:
            抽样后的记录列表
        """
        if self.data is None:
            self.load_data()
        
        total_count = len(self.data)
        sample_size = max(1, int(total_count * sample_percent / 100))
        
        # 设置随机种子保证可重复性
        random.seed(random_seed)
        
        # 获取随机索引
        indices = list(range(total_count))
        sampled_indices = random.sample(indices, sample_size)
        
        # 获取抽样数据
        sampled_data = self.data.iloc[sampled_indices]
        
        records = []
        for _, row in sampled_data.iterrows():
            record = row.to_dict()
            for key, value in record.items():
                if pd.isna(value):
                    record[key] = ""
            records.append(record)
        
        logger.info(f"抽样完成: 总数={total_count}, 抽样比例={sample_percent}%, 抽样数量={len(records)}")
        return records

    def get_record_by_id(self, record_id: int) -> Dict[str, Any]:
        """根据ID获取记录"""
        if self.data is None:
            self.load_data()
        
        if "question_id" in self.data.columns:
            mask = self.data["question_id"] == record_id
            if mask.any():
                record = self.data[mask].iloc[0].to_dict()
                for key, value in record.items():
                    if pd.isna(value):
                        record[key] = ""
                return record
        return None


class ProgressManager:
    """进度管理器"""

    def __init__(self, progress_file: str):
        self.progress_file = progress_file
        self.completed_ids: set = set()
        self.load_progress()

    def load_progress(self):
        """加载进度"""
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.completed_ids = set(data.get("completed_ids", []))
                logger.info(f"已加载 {len(self.completed_ids)} 条已完成记录")
            except Exception as e:
                logger.error(f"加载进度失败: {e}")
                self.completed_ids = set()

    def save_progress(self):
        """保存进度"""
        try:
            data = {"completed_ids": list(self.completed_ids)}
            with open(self.progress_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"保存进度失败: {e}")

    def is_completed(self, record_id: int) -> bool:
        """检查记录是否已完成"""
        return record_id in self.completed_ids

    def mark_completed(self, record_id: int):
        """标记记录为已完成"""
        self.completed_ids.add(record_id)

    def clear_progress(self):
        """清除进度"""
        self.completed_ids = set()
        if os.path.exists(self.progress_file):
            os.remove(self.progress_file)
        logger.info("进度已清除")

    def get_pending_records(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """获取待处理记录"""
        pending = []
        for record in records:
            record_id = record.get("question_id")
            if record_id is not None and not self.is_completed(record_id):
                pending.append(record)
        logger.info(f"待处理记录数: {len(pending)}")
        return pending