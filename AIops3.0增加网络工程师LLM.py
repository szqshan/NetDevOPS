

#======================待优化==============
#1.五分钟后关闭ssh，或exit登出后，再按输入登录，可再次重新登录
#2.paramiko输出的信息，再返回给LLM记忆好，再进行结果分析，给出报告和更进一步的检查命令，形成自动化循环检查。（未来还应引入知识库，根据知识库分析，给出解决方案，然后自动执行）
#3.大模型应该有两种system提示词模板，一种是Linux系统方面专家，另外一种是华为路由交换网络专家，向LLM提问之前，请先选择专家
#4.
#5.


from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
import json
import paramiko
import time
import re
import threading

# ========== 全局变量 ==========
ssh_connection = None
last_activity_time = 0
connection_lock = threading.Lock()
keep_alive = True



# ========== 全局配置 ==========
linux_expert_prompt = """
你是一位Linux运维专家，输出必须严格遵循以下格式：
# 第一部分：技术方案说明（1行）
<技术方案说明>

# 第二部分：具体命令（必须用代码块包裹）
```bash
<具体命令>

第三部分：JSON命令（必须用双大括号）

---json---
{{"commands": ["<命令1>", "<命令2>"]}}

===== 正确示例 =====
查看/home目录：
ls -l /home

---json---
{{"commands": ["ls -l /home"]}}

===== 错误示例 =====
错误1（路径占位符）：
ls /path/to  # 错误
---json---
{{"commands": [...]}}

错误2（格式错误）：
ls -l /home
---json---  # 分隔符在代码块内
{{"commands": [...]}}
请严格遵循上述格式，违规响应将无法解析！

"""
# ========== 新增分析专家提示词 ==========
linux_analysis_prompt = """
您是一位资深的Linux系统检查结果分析专家，请严格按照以下要求工作：

1. 输入将包含执行的命令及其对应输出
2. 分析重点包括：
   - 异常错误信息
   - 潜在安全隐患
   - 性能瓶颈指标
   - 配置不当问题
3. 输出格式：

# 分析报告
## 1. 异常情况
<分点列出关键异常>

## 2. 优化建议
<对应改进方案>

## 3. 综合评估
<整体系统健康度评估>

请使用专业术语，保持分析简洁准确！
"""

# ========== 华为网络专家配置 ==========
huawei_expert_prompt = """
你是一位华为网络工程师，输出必须严格遵循以下格式：
# 第一部分：技术方案说明（1行）
<技术方案说明>

# 第二部分：具体命令（必须用代码块包裹）
```cli
<华为设备命令>

第三部分：JSON命令（必须用双大括号）

---json---
{{"commands": ["<命令1>", "<命令2>"]}}

正确示例：
查看接口状态：
display interface brief

---json---
{{"commands": ["display interface brief"]}}
"""

huawei_analysis_prompt = """
您是一位资深网络设备分析专家，请分析以下内容：
1. 接口状态异常
2. 路由协议问题
3. ACL/NAT配置
4. 设备性能指标
"""

# ========== 初始化模型 ==========
model = ChatOpenAI(
    openai_api_key="sk-wrndltffnsccqraknsmbesqtaaddkvhitbtdezwkygaypdbo",
    openai_api_base="https://api.siliconflow.cn/v1",
    model_name="deepseek-ai/DeepSeek-V2.5",
    streaming=True,
    temperature=0.5
)

#构建对话链
# 修改后的PromptTemplate构建方式
prompt_template = ChatPromptTemplate.from_messages([
    ("system", linux_expert_prompt),
    ("human", "{user_input}")  # 确保只使用user_input变量
])
chain = prompt_template | model


# ========== 新增分析模型链 ==========
analysis_chain = ChatPromptTemplate.from_messages([
    ("system", linux_analysis_prompt),
    ("human", "命令执行结果：\n{command_results}")
]) | model



def get_connection_info():
    """获取SSH连接信息（添加调试输出）"""
    print("\n" + "=" * 40)
    print(" 请输入设备连接信息 ")
    print("=" * 40)
    host = input("设备IP: ").strip()
    port = int(input("端口号(默认22): ") or 22)
    username = input("用户名: ").strip()
    password = input("密码: ")
    print("[DEBUG] 连接信息输入完成")  # ✅ 调试点
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password
    }


# ========== SSH连接管理 ==========
def maintain_ssh_connection(conn_info):
    """SSH连接维护线程"""
    global ssh_connection, last_activity_time, keep_alive

    while keep_alive:
        with connection_lock:
            # 检查空闲超时
            if time.time() - last_activity_time > 300:  # 5分钟
                if ssh_connection and ssh_connection.get_transport().is_active():
                    ssh_connection.close()
                    print("\n连接因超时已自动关闭")
                return
        time.sleep(10)  # 每10秒检查一次


def get_ssh_connection(conn_info):
    """获取或创建SSH连接"""
    global ssh_connection, last_activity_time

    with connection_lock:
        if not ssh_connection or not ssh_connection.get_transport().is_active():
            print("\n建立SSH连接...")
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=conn_info["host"],
                port=conn_info["port"],
                username=conn_info["username"],
                password=conn_info["password"],
                timeout=10
            )
            ssh_connection = ssh
            # 启动保活线程
            threading.Thread(target=maintain_ssh_connection, args=(conn_info,), daemon=True).start()

        last_activity_time = time.time()  # 更新最后活动时间
        return ssh_connection


# ========== 核心功能函数 ==========
def execute_commands(conn_info, commands):
    """执行命令并返回结果"""
    global last_activity_time
    results = []

    try:
        ssh = get_ssh_connection(conn_info)
        for cmd in commands:
            try:
                stdin, stdout, stderr = ssh.exec_command(cmd)
                time.sleep(1)
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
                results.append({"command": cmd, "output": output, "error": error})
                last_activity_time = time.time()
                print(f"\n执行: {cmd}")
                print(f"输出: {output[:100]}..." if len(output) > 100 else f"输出: {output}")
            except Exception as e:
                results.append({"command": cmd, "output": "", "error": str(e)})
        return results
    except Exception as e:
        print(f"连接错误: {str(e)}")
        return []



# ========== 专家处理流程 ==========
def handle_linux_expert(conn_info):
    """处理Linux专家流程"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", linux_expert_prompt),
        ("human", "{user_input}")
    ]) | model

    print("\n" + "=" * 40)
    print(" Linux专家模式（输入exit退出） ")
    print("=" * 40)

    while True:
        user_input = input("\n[您的问题] => ").strip()
        if user_input.lower() in ("exit", "quit"):
            break

        full_response = ""
        print("\n[专家建议]：")
        for chunk in prompt.stream({"user_input": user_input}):
            full_response += chunk.content
            print(chunk.content, end="", flush=True)

        if commands := extract_commands(full_response):
            if input("\n\n是否执行这些命令？(y/n): ").lower() == "y":
                results = execute_commands(conn_info, commands)
                if input("是否分析执行结果？(y/n): ").lower() == "y":
                    print("\n[分析报告]：")
                    print(generate_analysis(results, "linux"))


def handle_huawei_expert(conn_info):
    """处理华为专家流程"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", huawei_expert_prompt),
        ("human", "{user_input}")
    ]) | model

    print("\n" + "=" * 40)
    print(" 华为网络专家模式（输入exit退出） ")
    print("=" * 40)

    while True:
        user_input = input("\n[您的问题] => ").strip()
        if user_input.lower() in ("exit", "quit"):
            break

        full_response = ""
        print("\n[专家建议]：")
        for chunk in prompt.stream({"user_input": user_input}):
            full_response += chunk.content
            print(chunk.content, end="", flush=True)

        if commands := extract_commands(full_response):
            if input("\n\n是否执行这些命令？(y/n): ").lower() == "y":
                results = execute_commands(conn_info, commands)
                if input("是否分析执行结果？(y/n): ").lower() == "y":
                    print("\n[分析报告]：")
                    print(generate_analysis(results, "huawei"))



def close_ssh_connection():
    """主动关闭连接"""
    global ssh_connection
    with connection_lock:
        if ssh_connection and ssh_connection.get_transport().is_active():
            ssh_connection.close()
            print("SSH连接已关闭")

# ========== 工具函数 ==========
def extract_commands(response):
    """从响应中提取命令"""
    try:
        match = re.search(r'---json---\s*({.*?})\s*$', response, flags=re.DOTALL)
        if match:
            return json.loads(match.group(1))["commands"]
        return []
    except Exception as e:
        print(f"命令解析失败: {str(e)}")
        return []


# ========== 优化后的命令执行函数 ==========
def execute_commands(conn_info, commands):
    """执行命令并返回结构化结果（带实时输出）"""
    global last_activity_time
    results = []

    try:
        ssh = get_ssh_connection(conn_info)
        print(f"\n\033[34m正在连接到 {conn_info['host']}...\033[0m")

        for idx, cmd in enumerate(commands, 1):
            try:
                # 打印命令提示
                print(f"\n\033[33m[{idx}/{len(commands)}] 执行命令:\033[0m \033[35m{cmd}\033[0m")

                # 执行命令
                stdin, stdout, stderr = ssh.exec_command(cmd)
                time.sleep(0.5)  # 等待命令执行

                # 获取输出
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
                last_activity_time = time.time()

                # 记录结果
                results.append({
                    "command": cmd,
                    "output": output,
                    "error": error
                })

                # 实时输出显示
                if output:
                    print(f"\033[32m[输出]\033[0m\n{output}")
                if error:
                    print(f"\033[31m[错误]\033[0m\n{error}")

                print("\033[90m" + "-" * 50 + "\033[0m")  # 灰色分隔线

            except Exception as cmd_error:
                error_msg = f"命令执行失败: {str(cmd_error)}"
                print(f"\033[31m{error_msg}\033[0m")
                results.append({
                    "command": cmd,
                    "output": "",
                    "error": error_msg
                })
                continue

        print("\n\033[42m命令执行完成！连接保持中（5分钟空闲自动断开）\033[0m")
        return results

    except Exception as conn_error:
        print(f"\033[41m连接异常: {str(conn_error)}\033[0m")
        close_ssh_connection()
        return []


# ========== 新增分析结果生成函数 ==========
def generate_analysis(results, expert_type):
    """生成分析报告"""
    analysis_prompt = linux_analysis_prompt if expert_type == "linux" else huawei_analysis_prompt
    formatted_results = "\n".join(
        f"命令: {r['command']}\n输出: {r['output']}\n错误: {r['error']}"
        for r in results
    )
    analysis_chain = ChatPromptTemplate.from_messages([
        ("system", analysis_prompt),
        ("human", f"命令执行结果：\n{formatted_results}")
    ]) | model
    return analysis_chain.invoke({}).content

def generate_analysis_report(command_results):
    """生成结果分析报告"""
    if not command_results:
        return "无有效执行结果可供分析"

    # 格式化结果数据
    formatted_results = []
    for res in command_results:
        formatted_results.append(
            f"命令: {res['command']}\n"
            f"输出: {res['output'] or '无'}\n"
            f"错误: {res['error'] or '无'}\n"
            "───"
        )

    print("\n正在生成分析报告...")
    analysis = analysis_chain.invoke({
        "command_results": "\n".join(formatted_results)
    })
    return analysis.content


# ========== 主程序退出处理 ==========
def cleanup():
    """程序退出时清理资源"""
    global keep_alive
    keep_alive = False
    close_ssh_connection()


# ========== 主函数逻辑 ==========

# ========== 主程序 ==========
def main():
    try:
        print("\n选择专家类型：")
        print("1. Linux系统专家")
        print("2. 华为网络专家")
        choice = input("请选择（1/2）: ").strip()

        conn_info = get_connection_info()

        if choice == "1":
            handle_linux_expert(conn_info)
        elif choice == "2":
            handle_huawei_expert(conn_info)
        else:
            print("无效选择，默认进入Linux专家模式")
            handle_linux_expert(conn_info)
    finally:
        cleanup()


if __name__ == '__main__':
    main()