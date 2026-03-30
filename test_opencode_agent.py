#!/usr/bin/env python3
"""
opencode Code Agent 功能测试
测试对象：Kimi CLI 源代码
测试方式：通过 HTTP API (SSE 流式响应)
"""

import httpx
import asyncio
import json
import time
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

BASE_URL = "http://127.0.0.1:4096"

@dataclass
class TestResult:
    name: str
    category: str
    task: str
    status: str  # PASS / FAIL / PARTIAL
    duration: float
    response: str
    tools_used: list = field(default_factory=list)
    tokens_input: int = 0
    tokens_output: int = 0
    iterations: int = 0
    error: Optional[str] = None

class OpenCodeTester:
    def __init__(self):
        self.session_id: Optional[str] = None
        self.results: list[TestResult] = []
    
    def create_session(self) -> str:
        """创建新会话"""
        with httpx.Client() as client:
            resp = client.post(f"{BASE_URL}/session")
            data = resp.json()
            self.session_id = data["id"]
            print(f"✅ 创建会话: {self.session_id[:16]}...")
            print(f"   工作目录: {data['directory']}")
            return self.session_id
    
    def send_message(self, message: str) -> dict:
        """发送消息并收集 SSE 响应"""
        full_text = ""
        tools_used = []
        tokens = {"input": 0, "output": 0}
        iterations = 0
        
        with httpx.Client(timeout=300.0) as client:
            with client.stream(
                "POST",
                f"{BASE_URL}/session/{self.session_id}/message",
                json={"parts": [{"type": "text", "content": message}]},
                headers={"Accept": "text/event-stream"}
            ) as response:
                for chunk in response.iter_text():
                    # 解析 SSE 事件
                    events = self._parse_sse(chunk)
                    for event_type, event_data in events:
                        if event_type == "text":
                            content = event_data.get("content", "")
                            full_text += content
                            print(content, end="", flush=True)
                        elif event_type == "tool":
                            tool_name = event_data.get("tool", "")
                            status = event_data.get("status", "")
                            if tool_name and tool_name not in tools_used:
                                tools_used.append(tool_name)
                            print(f"\n🔧 [{tool_name}] {status}", flush=True)
                        elif event_type == "started":
                            model = event_data.get("model", "")
                            agent = event_data.get("agent", "")
                            print(f"🤖 模型: {model}, Agent: {agent}", flush=True)
                        elif event_type == "done":
                            t = event_data.get("tokens", {})
                            tokens["input"] = t.get("input", 0)
                            tokens["output"] = t.get("output", 0)
                            iterations = event_data.get("iterations", 0)
                            print(f"\n📊 Tokens: 输入={tokens['input']}, 输出={tokens['output']}, 迭代={iterations}", flush=True)
                        elif event_type == "error":
                            msg = event_data.get("message", "Unknown error")
                            print(f"\n❌ 错误: {msg}", flush=True)
        
        return {
            "response": full_text,
            "tools": tools_used,
            "tokens": tokens,
            "iterations": iterations
        }
    
    def _parse_sse(self, chunk: str) -> list[tuple[str, dict]]:
        """解析 SSE 事件块"""
        events = []
        # 按双换行分割事件
        blocks = chunk.split("\r\n\r\n")
        for block in blocks:
            if not block.strip():
                continue
            event_type = None
            event_data = None
            for line in block.split("\r\n"):
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    try:
                        event_data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        event_data = {"raw": line[5:].strip()}
            if event_type and event_data:
                events.append((event_type, event_data))
        return events
    
    def delete_session(self):
        """删除会话"""
        if self.session_id:
            with httpx.Client() as client:
                client.delete(f"{BASE_URL}/session/{self.session_id}")
            print(f"🗑️ 删除会话: {self.session_id[:16]}...")
    
    def run_test(self, name: str, category: str, task: str) -> TestResult:
        """运行单个测试"""
        print(f"\n{'='*70}")
        print(f"📋 测试: {name}")
        print(f"📁 类别: {category}")
        print(f"💬 任务: {task}")
        print(f"{'='*70}")
        print("\n📝 Agent 响应:")
        print("-" * 50)
        
        start_time = time.time()
        
        try:
            result = self.send_message(task)
            duration = time.time() - start_time
            
            response_text = result.get("response", "")
            tools_used = result.get("tools", [])
            tokens = result.get("tokens", {})
            iterations = result.get("iterations", 0)
            
            print("-" * 50)
            print(f"⏱️ 耗时: {duration:.1f}s")
            if tools_used:
                print(f"🔧 工具: {', '.join(tools_used)}")
            
            # 判断测试结果
            status = self._evaluate_result(name, response_text, tools_used)
            print(f"📊 状态: {status}")
            
            test_result = TestResult(
                name=name,
                category=category,
                task=task,
                status=status,
                duration=duration,
                response=response_text,
                tools_used=tools_used,
                tokens_input=tokens.get("input", 0),
                tokens_output=tokens.get("output", 0),
                iterations=iterations,
            )
            
        except Exception as e:
            duration = time.time() - start_time
            print(f"\n❌ 错误: {e}")
            import traceback
            traceback.print_exc()
            test_result = TestResult(
                name=name,
                category=category,
                task=task,
                status="FAIL",
                duration=duration,
                response="",
                error=str(e)
            )
        
        self.results.append(test_result)
        return test_result
    
    def _evaluate_result(self, name: str, response: str, tools: list) -> str:
        """评估测试结果"""
        if not response or len(response) < 20:
            return "FAIL"
        
        # 根据测试类型判断
        if "A1" in name:  # 架构分析
            if any(kw in response for kw in ["tools", "protocol", "模块", "目录", "src", "kimi"]):
                return "PASS"
        elif "A2" in name:  # 工具解释
            if any(kw in response for kw in ["实现", "函数", "调用", "def ", "read", "文件"]):
                return "PASS"
        elif "B" in name:  # 搜索类
            if any(kw in response for kw in ["找到", "发现", "位于", "文件", "src/", "kimi"]):
                return "PASS"
        elif "C" in name:  # 分析类
            if any(kw in response for kw in ["函数", "def", "文档", "docstring", "class"]):
                return "PASS"
        elif "E" in name:  # 多轮对话
            if len(response) > 50:
                return "PASS"
        
        # 默认：有响应且使用了工具就算通过
        if tools and len(response) > 50:
            return "PASS"
        return "PARTIAL"


def run_all_tests():
    """运行所有测试"""
    tester = OpenCodeTester()
    
    print("\n" + "=" * 70)
    print("🚀 opencode Code Agent 功能测试")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🎯 测试目标: Kimi CLI 源代码")
    print("=" * 70)
    
    # 测试用例定义
    tests = [
        # 类别 A：代码理解能力
        {
            "name": "A1: 分析代码库架构",
            "category": "代码理解",
            "task": "分析这个代码库的整体架构，告诉我主要模块和它们的职责。请查看目录结构和关键文件。"
        },
        {
            "name": "A2: 解释 ReadFile 工具实现",
            "category": "代码理解", 
            "task": "找到并解释 ReadFile 工具的实现原理，它在哪个文件？核心逻辑是什么？"
        },
        
        # 类别 B：代码搜索能力
        {
            "name": "B1: 搜索 Shell 工具",
            "category": "代码搜索",
            "task": "找到 Shell 工具的实现代码，告诉我它在哪个文件，以及它是如何执行命令的。"
        },
        {
            "name": "B2: 搜索 timeout 相关代码",
            "category": "代码搜索",
            "task": "搜索所有包含 'timeout' 的代码，告诉我有哪些地方用到了超时设置。"
        },
        {
            "name": "B3: 定位 ACP 协议实现",
            "category": "代码搜索",
            "task": "找到 ACP 协议相关的所有文件，简要说明 ACP 的实现结构。"
        },
        
        # 类别 C：代码分析能力
        {
            "name": "C1: 查看可添加文档的函数",
            "category": "代码分析",
            "task": "找到 src/kimi_cli/tools/file/read_file.py 文件，查看其中的主要函数，告诉我哪些函数缺少文档字符串。"
        },
    ]
    
    # 运行测试
    try:
        tester.create_session()
        
        for test in tests:
            tester.run_test(
                name=test["name"],
                category=test["category"],
                task=test["task"]
            )
            # 每个测试间隔 1 秒
            time.sleep(1)
        
        tester.delete_session()
        
    except Exception as e:
        print(f"\n❌ 测试过程出错: {e}")
        import traceback
        traceback.print_exc()
    
    return tester.results


def run_multiturn_test():
    """运行多轮对话测试"""
    tester = OpenCodeTester()
    
    print("\n\n" + "=" * 70)
    print("🔄 多轮对话测试 (E类)")
    print("=" * 70)
    
    try:
        tester.create_session()
        
        # E1: 多轮迭代任务
        turns = [
            "分析 Kimi CLI 的 Grep 工具实现，找到它的源代码文件，告诉我它的核心逻辑。",
            "根据你刚才的分析，这个 Grep 实现有什么可以改进的地方？列出 2-3 点。",
            "你提到的第一个改进点，请详细说明如何实现，给出具体的代码修改建议。"
        ]
        
        for i, task in enumerate(turns, 1):
            print(f"\n🔄 Turn {i}/3")
            tester.run_test(
                name=f"E1-Turn{i}: 多轮迭代",
                category="多轮对话",
                task=task
            )
            time.sleep(1)
        
        tester.delete_session()
        
    except Exception as e:
        print(f"\n❌ 多轮测试出错: {e}")
        import traceback
        traceback.print_exc()
    
    return tester.results


def print_summary(results: list[TestResult]):
    """打印测试摘要"""
    print("\n\n" + "=" * 70)
    print("📊 测试结果汇总")
    print("=" * 70)
    
    if not results:
        print("\n⚠️ 没有测试结果")
        return
    
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    partial = sum(1 for r in results if r.status == "PARTIAL")
    failed = sum(1 for r in results if r.status == "FAIL")
    
    total_tokens_in = sum(r.tokens_input for r in results)
    total_tokens_out = sum(r.tokens_output for r in results)
    total_time = sum(r.duration for r in results)
    
    print(f"\n总计: {total} 项测试")
    print(f"  ✅ PASS:    {passed}")
    print(f"  🟡 PARTIAL: {partial}")
    print(f"  ❌ FAIL:    {failed}")
    print(f"  通过率:     {(passed + partial) / total * 100:.1f}%")
    print(f"\n资源消耗:")
    print(f"  总耗时:     {total_time:.1f}s")
    print(f"  输入 Token: {total_tokens_in:,}")
    print(f"  输出 Token: {total_tokens_out:,}")
    
    print("\n详细结果:")
    print("-" * 90)
    print(f"{'测试名称':<30} {'状态':<10} {'耗时':<8} {'迭代':<6} {'工具'}")
    print("-" * 90)
    
    for r in results:
        status_emoji = {"PASS": "✅", "PARTIAL": "🟡", "FAIL": "❌"}.get(r.status, "❓")
        tools_str = ", ".join(r.tools_used[:4]) if r.tools_used else "-"
        if len(r.tools_used) > 4:
            tools_str += f" +{len(r.tools_used)-4}"
        print(f"{r.name:<30} {status_emoji} {r.status:<7} {r.duration:>5.1f}s  {r.iterations:>3}     {tools_str}")
    
    print("-" * 90)


if __name__ == "__main__":
    print("开始测试...")
    
    # 运行基础测试
    results1 = run_all_tests()
    
    # 运行多轮对话测试
    results2 = run_multiturn_test()
    
    # 合并结果并生成最终报告
    all_results = results1 + results2
    
    print("\n\n" + "=" * 70)
    print("📋 最终测试报告")
    print("=" * 70)
    print_summary(all_results)
    
    print("\n🎉 所有测试完成!")
