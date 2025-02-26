

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

#初始化模型
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
    print(" 请输入服务器连接信息 ")
    print("=" * 40)
    host = input("服务器IP: ").strip()
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


# ========== 修改后的命令执行函数 ==========
def execute_commands(conn_info, commands):
    """使用持久连接执行命令"""
    global last_activity_time

    try:
        ssh = get_ssh_connection(conn_info)

        print(f"\n执行命令（连接：{conn_info['host']}）...")

        for cmd in commands:
            try:
                print(f"\n$ {cmd}")
                stdin, stdout, stderr = ssh.exec_command(cmd)
                time.sleep(0.5)

                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()

                if output:
                    print(f"[输出]\n{output}")
                if error:
                    print(f"\033[31m[错误]\n{error}\033[0m")

                last_activity_time = time.time()  # 更新活动时间

            except Exception as cmd_error:
                print(f"\033[31m命令执行失败: {str(cmd_error)}\033[0m")
                continue

        print("\n命令执行完成！连接保持中（5分钟空闲自动断开）")

    except Exception as conn_error:
        print(f"\033[31m连接异常: {str(conn_error)}\033[0m")
        close_ssh_connection()


def close_ssh_connection():
    """主动关闭连接"""
    global ssh_connection
    with connection_lock:
        if ssh_connection and ssh_connection.get_transport().is_active():
            ssh_connection.close()
            print("SSH连接已关闭")


def extract_commands(response):
    """带预处理的命令解析"""
    try:
        # 使用正则精准提取
        match = re.search(r'---json---\s*({.*?})\s*$', response, flags=re.DOTALL)
        if not match:
            return []

        json_str = match.group(1)
        json_str = json_str.strip()
        json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)  # 删除注释
        json_str = json_str.replace("'", '"')  # 统一引号

        # 验证JSON结构
        data = json.loads(json_str)
        if not isinstance(data.get("commands"), list):
            return []

        # 过滤无效命令
        return [cmd for cmd in data["commands"] if "/path/to" not in cmd]

    except Exception as e:
        print(f"解析失败（详细错误：{str(e)}）")
        return []


# ========== 修改后的命令执行函数 ==========
def execute_commands(conn_info, commands):
    """执行命令并返回结构化结果"""
    global last_activity_time
    results = []

    try:
        ssh = get_ssh_connection(conn_info)
        print(f"\n执行命令（连接：{conn_info['host']}）...")

        for cmd in commands:
            try:
                print(f"\n$ {cmd}")
                stdin, stdout, stderr = ssh.exec_command(cmd)
                time.sleep(0.5)  # 等待命令执行

                # 获取输出并解码
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()

                # ========== 新增实时输出显示 ==========
                print("[执行结果]")
                if output:
                    print(f"\033[34m{output}\033[0m")  # 蓝色显示正常输出
                if error:
                    print(f"\033[31m{error}\033[0m")  # 红色显示错误信息
                print("─" * 50)  # 分隔线

                # 存储结构化结果
                results.append({
                    "command": cmd,
                    "output": output,
                    "error": error
                })

                last_activity_time = time.time()  # 更新活动时间

            except Exception as cmd_error:
                error_msg = f"命令执行失败: {str(cmd_error)}"
                print(f"\033[31m{error_msg}\033[0m")
                results.append({
                    "command": cmd,
                    "output": "",
                    "error": error_msg
                })
                continue

        return results

    except Exception as conn_error:
        print(f"\033[31m连接异常: {str(conn_error)}\033[0m")
        return []


# ========== 新增分析结果生成函数 ==========
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
def main():
    try:
        conn_info = get_connection_info()
        print("\n" + "=" * 40)
        print(" Linux运维助手（输入 exit 退出） ")
        print("=" * 40)

        while True:
            try:
                user_input = input("\n[您的问题] => ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit"):
                    break
                # 生成LLM响应
                full_response = ""
                print("\n[专家回答]：")
                for chunk in chain.stream({"user_input": user_input}):
                    content = chunk.content
                    print(content, end="", flush=True)
                    full_response += content

                # 提取命令
                commands = extract_commands(full_response)
                if commands:
                    print("\n\n[生成的自动化命令]：")
                    print(json.dumps({"commands": commands}, indent=2, ensure_ascii=False))

                    # 获取用户确认
                    confirm = input("\n是否执行这些命令？(y/n): ").lower()
                    if confirm == "y":
                        #execute_commands(conn_info, commands)
                        command_results = execute_commands(conn_info, commands)  # 获取结果
                        # 新增分析环节
                        analyze = input("是否分析执行结果？(y/n): ").lower()
                        if analyze == "y":
                            print("\n[系统分析报告]")
                            report = generate_analysis_report(command_results)
                            print(report)
                print("\n" + "=" * 60)

            except KeyboardInterrupt:
                print("\n检测到中断，退出程序")
                break

    finally:
        cleanup()

if __name__ == '__main__':
    main()