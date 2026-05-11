#!/usr/bin/env python
"""测试API连接性"""

import json
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# 禁用SSL证书警告
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

def ensure_bearer_token(token):
    """确保token以Bearer前缀开头"""
    if not token.lower().startswith("bearer "):
        return f"Bearer {token}"
    return token

def test_api_connection():
    """测试API连接"""
    # 加载配置
    with open("config.json", "r") as f:
        config = json.load(f)
    
    api_base_url = config["api_base_url"]
    user_token = config["user_token"]
    
    # 确保token以Bearer前缀开头
    user_token = ensure_bearer_token(user_token)
    
    headers = {
        "Content-Type": "application/json",
        "X-SQLBOT-TOKEN": user_token
    }
    
    print(f"测试API地址: {api_base_url}")
    print(f"用户Token: {user_token[:30]}...")
    
    # 测试1: 获取数据源列表
    print("\n1. 获取数据源列表")
    datasource_id = None
    try:
        full_url = f"{api_base_url}/datasource/list"
        response = requests.get(full_url, headers=headers, verify=False, timeout=10)
        print(f"   状态码: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"   ✓ 成功! 数据源数量: {len(data)}")
            if data:
                datasource_id = data[0]["id"]
                print(f"   使用第一个数据源: id={datasource_id}, name={data[0].get('name', 'N/A')}")
            else:
                print("   ✗ 没有可用的数据源")
        else:
            print(f"   ✗ 失败: {response.status_code}")
            print(f"   响应内容: {response.text[:200]}")
    except Exception as e:
        print(f"   ✗ 失败: {e}")
    
    # 测试2: 获取对话列表
    print("\n2. 获取对话列表测试")
    try:
        full_url = f"{api_base_url}/chat/list"
        response = requests.get(full_url, headers=headers, verify=False, timeout=10)
        print(f"   状态码: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"   ✓ 成功! 对话数量: {len(data)}")
        else:
            print(f"   ✗ 失败: {response.status_code}")
            print(f"   响应内容: {response.text[:200]}")
    except Exception as e:
        print(f"   ✗ 失败: {e}")
    
    # 测试3: 创建对话（使用数据源ID）
    print("\n3. 创建对话测试")
    if datasource_id is None:
        print("   ✗ 跳过，没有可用的数据源")
        return
    
    try:
        full_url = f"{api_base_url}/chat/start"
        payload = {"origin": 0, "datasource": datasource_id}
        print(f"   请求体: {payload}")
        response = requests.post(full_url, headers=headers, json=payload, verify=False, timeout=10)
        print(f"   状态码: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            chat_id = data.get('id')
            print(f"   ✓ 成功! chat_id: {chat_id}")
            
            # 测试4: 发送问题
            print("\n4. 发送问题测试")
            try:
                question_url = f"{api_base_url}/chat/question"
                question_payload = {
                    "chat_id": chat_id,
                    "question": "测试问题",
                    "lang": "简体中文"
                }
                response = requests.post(question_url, headers=headers, json=question_payload, verify=False, timeout=30)
                print(f"   状态码: {response.status_code}")
                if response.status_code == 200:
                    # 解析SSE响应
                    lines = response.text.strip().split("\n")
                    for line in lines[:5]:
                        if line.startswith("data:"):
                            print(f"   响应数据: {line[:150]}")
                    print(f"   ✓ 成功!")
                else:
                    print(f"   ✗ 失败: {response.status_code}")
                    print(f"   响应内容: {response.text[:200]}")
            except Exception as e:
                print(f"   ✗ 发送问题失败: {e}")
                
        else:
            print(f"   ✗ 失败: {response.status_code}")
            print(f"   响应头: {dict(response.headers)}")
            print(f"   响应内容: {response.text[:500]}")
    except Exception as e:
        print(f"   ✗ 失败: {e}")

if __name__ == "__main__":
    test_api_connection()