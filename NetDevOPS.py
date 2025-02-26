#总体框架：langchain 接入LLM可以对话，给LLM告警信息，然后LLM输出检查命令，命令给到netmiko，netmiko进行自动ssh登录设备，自动输入命令检查。并返回结果给LLM进行分析。循环验证。

#1.longchain接入硅基流动的DEEPSEEK LLM，实现对话，配置提示词，LLM返回ip地址，接口，检查设备所需要的命令行等重要信息
endpoint = "deepseek-ai/DeepSeek-R1"
APIkey = "sk-wrndltffnsccqraknsmbesqtaaddkvhitbtdezwkygaypdbo"

import requests
import json
import time
from netmiko import ConnectHandler

# 配置 LLM API 连接
url = "https://api.siliconflow.cn/v1/chat/completions"
headers = {
    "Authorization": "Bearer <token>",
    "Content-Type": "application/json"
}


def query_llm(prompt):
    payload = {
        "model": "deepseek-ai/DeepSeek-V3",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": 512,
        "temperature": 0.7,
        "top_p": 0.7,
        "top_k": 50,
        "frequency_penalty": 0.5,
        "n": 1,
        "response_format": {"type": "text"}
    }
    response = requests.post(url, json=payload, headers=headers)
    return response.json()


# 提示词，告警信息示例
prompt = "设备告警信息: CPU 使用率过高，内存占用异常。请提供需要检查的 IP 地址、接口和诊断命令。"

# 获取 LLM 响应
response = query_llm(prompt)
print("LLM 返回的检查信息:", response)

# 解析返回的 JSON 数据
try:
    device_info = json.loads(response.get("choices", [{}])[0].get("message", {}).get("content", "{}"))
except json.JSONDecodeError:
    print("LLM 返回的数据格式错误！")
    exit()

# 连接到设备
device = {
    "device_type": device_info["device_type"],
    "host": device_info["ip"],
    "username": device_info["username"],
    "password": device_info["password"],
}

try:
    ssh_connection = ConnectHandler(**device)
    results = {}
    for command in device_info["commands"]:
        output = ssh_connection.send_command(command)
        results[command] = output
    ssh_connection.disconnect()
except Exception as e:
    print("SSH 连接失败:", str(e))
    exit()

# 发送检查结果给 LLM 进行分析
analysis_prompt = f"检查结果: {json.dumps(results, ensure_ascii=False)}\n请分析问题并给出优化建议。"
analysis_response = query_llm(analysis_prompt)
print("LLM 分析结果:", analysis_response)


# 可以设置循环验证机制，比如每 10 分钟检查一次
def monitor_loop(interval=600):
    while True:
        print("\n--- 开始新一轮检查 ---")
        response = query_llm(prompt)
        try:
            device_info = json.loads(response.get("choices", [{}])[0].get("message", {}).get("content", "{}"))
        except json.JSONDecodeError:
            print("LLM 返回的数据格式错误！")
            continue

        try:
            ssh_connection = ConnectHandler(**device)
            results = {}
            for command in device_info["commands"]:
                output = ssh_connection.send_command(command)
                results[command] = output
            ssh_connection.disconnect()
        except Exception as e:
            print("SSH 连接失败:", str(e))
            continue

        analysis_prompt = f"检查结果: {json.dumps(results, ensure_ascii=False)}\n请分析问题并给出优化建议。"
        analysis_response = query_llm(analysis_prompt)
        print("LLM 分析结果:", analysis_response)

        time.sleep(interval)

# 启动监控循环（可选）
# monitor_loop()


# 启动监控循环（可选）
# monitor_loop()



#2.Netmiko根据返回命令，自动SSH登录设备，输入命令进行检查，然后返回结果给LLM