#!/usr/bin/env python
"""调试API返回的事件格式"""

import asyncio
import json

from api_client import SQLBotAPIClient

async def debug_events():
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
    print(f"chat_id: {chat_id}")
    
    # 发送问题
    print("\n=== 发送问题 ===")
    question = "集团今年1-8月在深圳的营业收入是多少？"
    print(f"问题: {question}")
    
    events = await client.send_question(chat_id, question)
    print(f"\n收到 {len(events)} 个事件:")
    
    # 打印每个事件的详细内容
    for i, event in enumerate(events):
        print(f"\n--- 事件 {i+1} ---")
        print(f"类型: {event.get('type')}")
        print(f"完整内容:")
        print(json.dumps(event, ensure_ascii=False, indent=2))
    
    # 获取对话记录
    print("\n=== 获取对话记录 ===")
    records = chat_result.get("records", [])
    if records:
        record_id = records[0].get("id")
        record_data = await client.get_chat_record(chat_id, record_id)
        print("对话记录内容:")
        print(json.dumps(record_data, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(debug_events())