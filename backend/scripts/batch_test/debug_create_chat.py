#!/usr/bin/env python
"""调试create_chat接口"""

import json
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

def ensure_bearer_token(token):
    if not token.lower().startswith("bearer "):
        return f"Bearer {token}"
    return token

def debug_create_chat():
    with open("config.json", "r") as f:
        config = json.load(f)
    
    api_base_url = config["api_base_url"]
    user_token = ensure_bearer_token(config["user_token"])
    datasource_id = config.get("datasource_id")
    
    headers = {
        "Content-Type": "application/json",
        "X-SQLBOT-TOKEN": user_token
    }
    
    print(f"API地址: {api_base_url}")
    print(f"Token: {user_token[:30]}...")
    print(f"数据源ID: {datasource_id}")
    
    url = f"{api_base_url}/chat/start"
    payload = {"origin": 0, "datasource": datasource_id}
    
    print(f"\n请求URL: {url}")
    print(f"请求体: {json.dumps(payload)}")
    
    response = requests.post(url, headers=headers, json=payload, verify=False, timeout=10)
    
    print(f"\n响应状态码: {response.status_code}")
    print(f"响应头: {dict(response.headers)}")
    print(f"响应内容: {response.text}")
    
    if response.status_code == 200:
        try:
            data = response.json()
            print(f"\n解析后的响应: {data}")
            
            # 解析嵌套结构 {"code": 0, "data": {...}, "msg": null}
            if "data" in data:
                data = data["data"]
            
            chat_id = data.get("id")
            print(f"chat_id: {chat_id}")
            print(f"chat_id类型: {type(chat_id)}")
            
            # 检查是否有records
            records = data.get("records", [])
            if records:
                record_id = records[0].get("id")
                print(f"record_id: {record_id}")
            
        except json.JSONDecodeError:
            print("\n响应不是有效的JSON")

if __name__ == "__main__":
    debug_create_chat()