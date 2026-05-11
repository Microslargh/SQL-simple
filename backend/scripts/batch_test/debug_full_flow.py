#!/usr/bin/env python
"""调试完整的测试流程"""

import asyncio
import json

from api_client import SQLBotAPIClient

async def debug_full_flow():
    # 加载配置
    with open("config.json", "r") as f:
        config = json.load(f)
    
    print(f"配置信息:")
    print(f"  API地址: {config['api_base_url']}")
    print(f"  Token: {config['user_token'][:30]}...")
    print(f"  数据源ID: {config['datasource_id']}")
    print()
    
    # 创建API客户端
    client = SQLBotAPIClient(
        api_base_url=config["api_base_url"],
        user_token=config["user_token"],
        timeout_seconds=config["timeout_seconds"]
    )
    
    # 1. 测试创建对话
    print("=== 步骤1: 创建对话 ===")
    try:
        chat_result = await client.create_chat(datasource=config["datasource_id"])
        chat_id = chat_result.get("id")
        print(f"✓ 创建对话成功")
        print(f"  chat_id: {chat_id}")
        
        records = chat_result.get("records", [])
        if records:
            record_id = records[0].get("id")
            print(f"  record_id: {record_id}")
    except Exception as e:
        print(f"✗ 创建对话失败: {e}")
        return
    
    # 2. 测试发送问题
    print("\n=== 步骤2: 发送问题 ===")
    question = "集团今年1-8月在深圳的营业收入是多少？"
    print(f"  问题: {question}")
    
    try:
        events = await client.send_question(chat_id, question)
        print(f"✓ 发送问题成功")
        print(f"  收到 {len(events)} 个事件")
        
        # 打印关键事件
        for event in events[:10]:
            event_type = event.get("type")
            step = event.get("step")
            if event_type or step:
                print(f"    - {event_type}: {step}")
        
        # 查找错误信息
        for event in events:
            if event.get("type") == "error":
                print(f"    ✗ 错误: {event.get('message')}")
            
    except Exception as e:
        print(f"✗ 发送问题失败: {e}")
        return
    
    # 3. 测试获取对话记录
    print("\n=== 步骤3: 获取对话记录 ===")
    try:
        record_data = await client.get_chat_record(chat_id, record_id)
        print(f"✓ 获取对话记录成功")
        print(f"  记录ID: {record_data.get('id')}")
        print(f"  状态: {record_data.get('status')}")
    except Exception as e:
        print(f"✗ 获取对话记录失败: {e}")
    
    print("\n=== 调试完成 ===")

if __name__ == "__main__":
    asyncio.run(debug_full_flow())