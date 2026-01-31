#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task_manager.py - 重构后的异步任务调度器
主要改进：
1. 移除自定义协议层，直接使用OpenAI标准协议
2. 保存AI的tool_calls消息到会话上下文
3. 简化工具调用流程，不解释工具返回内容
"""

import asyncio
import logging
import time
import json
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass
from enum import Enum


class ToolCallStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ToolCallTask:
    tool_call_id: str
    session_id: str
    chat_id: str
    tool_name: str
    arguments: Dict[str, Any]
    status: ToolCallStatus = ToolCallStatus.PENDING
    start_time: float = 0
    result: Optional[str] = None


class TaskManager:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.context_manager = None
        self.session_manager = None
        self.essentials_manager = None
        self.tool_manager = None
        self.port_manager = None
        self.message_callback = None
        self.config = None
        
        # 工具调用跟踪器（会话间并行，会话内串行）
        self.tool_tracker = {}  # session_id -> {tool_call_id: ToolCallTask}
        self.session_semaphores = {}  # session_id -> asyncio.Semaphore(1)
        self.max_tool_calls = 10
        
    async def initialize(self, config: Dict[str, Any], **kwargs):
        self.config = config
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                
    def set_message_callback(self, callback: Callable):
        self.message_callback = callback
        
    async def execute_task(self, task_info: Dict[str, Any]) -> Dict[str, Any]:
        try:
            workflow_type = task_info.get("workflow_type", "A")
            
            if workflow_type == "A":
                return await self._workflow_a(task_info)
            elif workflow_type == "B":
                return await self._workflow_b(task_info)
            elif workflow_type == "C":
                return await self._workflow_c(task_info)
            else:
                return await self._create_error_result(task_info, f"未知工作流类型: {workflow_type}")
                
        except Exception as e:
            self.logger.error(f"任务执行失败: {e}")
            return await self._create_error_result(task_info, str(e))
            
    async def _workflow_a(self, task_info: Dict[str, Any]) -> Dict[str, Any]:
        task_data = task_info.get("task_data", {})
        chat_id = task_data.get("chat_id")
        
        if self.context_manager:
            try:
                await self.context_manager.update_context(chat_id=chat_id, message_data=task_data)
            except Exception as e:
                self.logger.error(f"更新上下文异常: {e}")
                
        is_command = False
        command_result = None
        
        if self.essentials_manager:
            is_command = self.essentials_manager.is_command(task_data)
            if is_command:
                command_result = await self.essentials_manager.execute_command(task_data)
                
        result = {
            "workflow_type": "A",
            "task_id": task_info.get("task_id"),
            "chat_id": chat_id,
            "is_command": is_command,
            "success": True
        }
        
        if is_command and command_result:
            if "content" not in command_result:
                command_result["content"] = "指令执行成功"
            if "chat_id" not in command_result:
                command_result["chat_id"] = chat_id
                
            result["response"] = command_result
            
        return result
        
    async def _workflow_b(self, task_info: Dict[str, Any]) -> Dict[str, Any]:
        task_data = task_info.get("task_data", {})
        chat_id = task_data.get("chat_id")
        
        if self.essentials_manager:
            is_command = self.essentials_manager.is_command(task_data)
            if is_command:
                command_result = await self.essentials_manager.execute_command(task_data)
                
                result = {
                    "workflow_type": "A",
                    "task_id": task_info.get("task_id"),
                    "chat_id": chat_id,
                    "is_command": True,
                    "success": True
                }
                
                if command_result:
                    if "content" not in command_result:
                        command_result["content"] = "指令执行成功"
                    if "chat_id" not in command_result:
                        command_result["chat_id"] = chat_id
                        
                    result["response"] = command_result
                    
                return result
        
        context_data = None
        if self.context_manager:
            try:
                context_result = await self.context_manager.update_context(chat_id=chat_id, message_data=task_data)
                if context_result.get("success"):
                    context_data = context_result.get("data")
            except Exception as e:
                self.logger.error(f"更新上下文异常: {e}")
                
        session_id = None
        if self.session_manager and context_data:
            try:
                session_result = await self.session_manager.create_session(chat_id=chat_id, context_data=context_data)
                if session_result.get("success"):
                    session_id = session_result.get("session_id")
            except Exception as e:
                self.logger.error(f"创建会话异常: {e}")
                
        result = {
            "workflow_type": "B",
            "task_id": task_info.get("task_id"),
            "chat_id": chat_id,
            "session_id": session_id,
            "context_data": context_data,
            "success": True if session_id else False
        }
        
        if not session_id:
            result["error"] = "无法创建会话缓存"
            
        return result
        
    async def _workflow_c(self, task_info: Dict[str, Any]) -> Dict[str, Any]:
        task_data = task_info.get("task_data", {})
        session_id = task_data.get("session_id")
        chat_id = task_data.get("chat_id")
        
        if not session_id or not self.session_manager:
            return await self._create_error_result(task_info, "缺少session_id或session_manager未初始化")
            
        try:
            # 获取会话数据
            session_result = await self.session_manager.get_session(session_id)
            if not session_result.get("success"):
                return await self._create_error_result(task_info, f"获取会话失败: {session_result.get('error')}")
                
            session_data = session_result.get("data")
            
            # 调用模型服务
            model_response = await self._call_model_service(session_data, chat_id)
            
            if not model_response:
                return await self._create_error_result(task_info, "模型服务调用失败")
                
            # 检查是否有工具调用
            if await self._has_tool_calls(model_response):
                # 处理工具调用
                final_response = await self._handle_tool_calls(
                    session_id=session_id,
                    chat_id=chat_id,
                    model_response=model_response,
                    session_data=session_data
                )
            else:
                final_response = model_response
                
            # 清理会话
            await self.session_manager.cleanup_session(session_id)
            
            # 提取响应内容
            response_content = await self._extract_response_content(final_response)
            
            result = {
                "workflow_type": "C",
                "task_id": task_info.get("task_id"),
                "chat_id": chat_id,
                "session_id": session_id,
                "response": {
                    "chat_id": chat_id,
                    "content": response_content,
                    "timestamp": time.time()
                },
                "success": True
            }
            
            return result
            
        except Exception as e:
            if self.session_manager and session_id:
                await self.session_manager.cleanup_session(session_id)
            return await self._create_error_result(task_info, str(e))
            
    async def _handle_tool_calls(self, session_id: str, chat_id: str, 
                                model_response: Dict[str, Any], 
                                session_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理工具调用 - 重构版"""
        if not self.tool_manager or not self.session_manager:
            return model_response
            
        # 提取工具调用信息
        tool_calls = await self._extract_tool_calls(model_response)
        if not tool_calls:
            return model_response
            
        # 1. 保存AI的tool_calls消息到会话
        assistant_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls
        }
        await self.session_manager.add_tool_call_message(session_id, assistant_message)
        
        # 2. 执行工具调用（会话内串行）
        tool_results = await self._execute_tools_for_session(
            session_id=session_id,
            chat_id=chat_id,
            tool_calls=tool_calls
        )
        
        # 3. 将工具结果添加到会话
        await self.session_manager.add_tool_results(session_id, tool_results)
        
        # 4. 获取更新后的会话数据
        session_result = await self.session_manager.get_session(session_id)
        if session_result.get("success"):
            updated_session_data = session_result.get("data")
        else:
            return model_response
            
        # 5. 再次调用模型服务
        return await self._call_model_service(updated_session_data, chat_id)
        
    async def _execute_tools_for_session(self, session_id: str, chat_id: str, 
                                        tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """执行工具调用 - 会话内串行，会话间并行"""
        # 确保每个会话有自己的信号量（实现会话内串行）
        if session_id not in self.session_semaphores:
            self.session_semaphores[session_id] = asyncio.Semaphore(1)
            
        async with self.session_semaphores[session_id]:
            return await self._execute_tools_serial(tool_calls, chat_id, session_id)
            
    async def _execute_tools_serial(self, tool_calls: List[Dict[str, Any]], 
                                   chat_id: str, session_id: str) -> List[Dict[str, Any]]:
        """串行执行工具调用"""
        tool_results = []
        
        for tool_call in tool_calls:
            tool_result = await self._execute_single_tool(
                tool_call=tool_call,
                chat_id=chat_id,
                session_id=session_id
            )
            
            if tool_result:
                tool_results.append(tool_result)
                
        return tool_results
        
    async def _execute_single_tool(self, tool_call: Dict[str, Any], 
                                  chat_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        """执行单个工具调用"""
        try:
            tool_call_id = tool_call.get("id")
            function_info = tool_call.get("function", {})
            tool_name = function_info.get("name")
            arguments_str = function_info.get("arguments", "{}")
            
            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                arguments = {}
                
            # 记录工具调用开始
            task = ToolCallTask(
                tool_call_id=tool_call_id,
                session_id=session_id,
                chat_id=chat_id,
                tool_name=tool_name,
                arguments=arguments,
                status=ToolCallStatus.RUNNING,
                start_time=time.time()
            )
            
            await self._track_tool_call(task)
            
            try:
                # 执行工具（使用工具模块的超时配置）
                result = await self.tool_manager.execute_tool_with_timeout(
                    tool_name=tool_name,
                    arguments=arguments,
                    chat_id=chat_id,
                    session_id=session_id
                )
                
                # 工具返回原始content
                content = result
                    
                task.status = ToolCallStatus.COMPLETED
                task.result = content
                
            except asyncio.TimeoutError:
                content = "工具执行超时"
                task.status = ToolCallStatus.TIMEOUT
                task.result = content
            except Exception as e:
                content = f"工具执行失败: {str(e)}"
                task.status = ToolCallStatus.FAILED
                task.result = content
                
            finally:
                await self._update_tool_call(task)
                
            # 返回标准格式的工具结果
            return {
                "tool_call_id": tool_call_id,
                "role": "tool",
                "name": tool_name,
                "content": content
            }
            
        except Exception as e:
            self.logger.error(f"执行工具调用失败: {e}")
            return None
            
    async def _track_tool_call(self, task: ToolCallTask):
        """跟踪工具调用"""
        if task.session_id not in self.tool_tracker:
            self.tool_tracker[task.session_id] = {}
            
        self.tool_tracker[task.session_id][task.tool_call_id] = task
        
    async def _update_tool_call(self, task: ToolCallTask):
        """更新工具调用状态"""
        if (task.session_id in self.tool_tracker and 
            task.tool_call_id in self.tool_tracker[task.session_id]):
            self.tool_tracker[task.session_id][task.tool_call_id] = task
            
    async def _has_tool_calls(self, response: Dict[str, Any]) -> bool:
        """检查响应是否包含工具调用"""
        if not response:
            return False
            
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls", [])
            return len(tool_calls) > 0
            
        return False
        
    async def _extract_tool_calls(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """提取工具调用信息"""
        tool_calls = []
        
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            for tool_call in message.get("tool_calls", []):
                tool_calls.append({
                    "id": tool_call.get("id"),
                    "type": "function",
                    "function": {
                        "name": tool_call.get("function", {}).get("name"),
                        "arguments": tool_call.get("function", {}).get("arguments")
                    }
                })
                
        return tool_calls
        
    async def _call_model_service(self, session_data: Dict[str, Any], chat_id: str) -> Optional[Dict[str, Any]]:
        if not self.port_manager:
            return None
            
        try:
            request_data = {
                "chat_id": chat_id,
                "session_data": session_data,
                "timestamp": time.time()
            }
            
            return await self.port_manager.send_to_model_async(request_data)
            
        except Exception as e:
            self.logger.error(f"异步调用模型服务失败: {e}")
            return None
            
    async def _extract_response_content(self, model_response: Dict[str, Any]) -> str:
        if not model_response:
            return "模型服务返回空响应"
            
        try:
            if "choices" in model_response and model_response["choices"]:
                choice = model_response["choices"][0]
                if "message" in choice:
                    message = choice["message"]
                    if "content" in message and message["content"]:
                        return message["content"]
                    elif "tool_calls" in message:
                        return "[抱歉，群聊太过抽象，响应失败啦]"
                        
            if "content" in model_response:
                return model_response["content"]
                
            return str(model_response)
            
        except Exception as e:
            self.logger.error(f"提取响应内容失败: {e}")
            return "无法解析模型响应"
            
    async def _create_error_result(self, task_info: Dict[str, Any], error_msg: str) -> Dict[str, Any]:
        return {
            "workflow_type": task_info.get("workflow_type", "unknown"),
            "task_id": task_info.get("task_id"),
            "chat_id": task_info.get("task_data", {}).get("chat_id"),
            "success": False,
            "error": error_msg
        }
        
    async def cleanup_session_tools(self, session_id: str):
        """清理会话的工具调用跟踪"""
        if session_id == "*":
            # 清理所有会话
            self.tool_tracker.clear()
            self.session_semaphores.clear()
        elif session_id in self.tool_tracker:
            del self.tool_tracker[session_id]
        if session_id in self.session_semaphores:
            del self.session_semaphores[session_id]
            
    async def get_tool_tracking_status(self) -> Dict[str, Any]:
        """获取工具调用跟踪状态"""
        status = {
            "total_sessions": len(self.tool_tracker),
            "sessions": {}
        }
        
        for session_id, tasks in self.tool_tracker.items():
            session_status = {
                "total_tools": len(tasks),
                "tools": {}
            }
            
            for tool_call_id, task in tasks.items():
                session_status["tools"][tool_call_id] = {
                    "status": task.status.value,
                    "tool_name": task.tool_name,
                    "running_time": time.time() - task.start_time if task.start_time > 0 else 0
                }
                
            status["sessions"][session_id] = session_status
            
        return status