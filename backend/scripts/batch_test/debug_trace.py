#!/usr/bin/env python
"""调试执行轨迹API"""

import asyncio
import json

from api_client import SQLBotAPIClient

async def debug_trace():
    # 加载配置
    with open("config.json", "r") as f:
        config = json.load(f)
    
    # 创建API客户端
    client = SQLBotAPIClient(
        api_base_url=config["api_base_url"],
        user_token=config["user_token"],
        timeout_seconds=config["timeout_seconds"]
    )
    
    # 创建对话
    print("=== 创建对话 ===")
    chat_result = await client.create_chat(datasource=config["datasource_id"])
    chat_id = chat_result.get("id")
    records = chat_result.get("records", [])
    record_id = records[0].get("id") if records else None
    print(f"chat_id: {chat_id}")
    print(f"record_id: {record_id}")
    
    # 发送问题
    print("\n=== 发送问题 ===")
    question = "集团今年1-8月在深圳的营业收入是多少？"
    print(f"问题: {question}")
    events = await client.send_question(chat_id, question)
    
    # 获取新的record_id（发送问题后会创建新记录）
    print("\n=== 获取对话记录列表 ===")
    chat_record = await client.get_chat_record(chat_id, record_id)
    all_records = chat_record.get("records", [])
    if len(all_records) > 1:
        latest_record = all_records[-1]
        new_record_id = latest_record.get("id")
        print(f"最新record_id: {new_record_id}")
        
        # 获取执行轨迹
        print("\n=== 获取执行轨迹 ===")
        try:
            trace = await client.get_execution_trace(new_record_id)
            print(f"轨迹长度: {len(trace)}")
            for i, node in enumerate(trace):
                print(f"\n--- 节点 {i+1} ---")
                node_key = node.get("node_key")
                print(f"node_key: {node_key}")
                print(f"完整内容:")
                print(json.dumps(node, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"获取轨迹失败: {e}")

if __name__ == "__main__":
    asyncio.run(debug_trace())