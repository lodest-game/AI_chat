#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task_manager.py - 异步任务调度器
"""

import asyncio
import logging
import time
import json
from typing import Dict, Any, Optional, Callable
import traceback


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
            session_result = await self.session_manager.get_session(session_id)
            if not session_result.get("success"):
                return await self._create_error_result(task_info, f"获取会话失败: {session_result.get('error')}")
                
            session_data = session_result.get("data")
            model_response = await self._call_model_service(session_data, chat_id)
            
            if not model_response:
                return await self._create_error_result(task_info, "模型服务调用失败")
                
            final_response = await self._process_tool_calls(
                session_id=session_id,
                chat_id=chat_id,
                initial_response=model_response,
                session_data=session_data
            )
            
            await self._handle_tool_operations(final_response, chat_id)
            await self.session_manager.cleanup_session(session_id)
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
            
    async def _handle_tool_operations(self, model_response: Dict[str, Any], chat_id: str):
        if not model_response or not self.context_manager:
            return
            
        try:
            if await self._has_tool_calls(model_response):
                tool_calls = await self._extract_tool_calls(model_response)
                for tool_call in tool_calls:
                    function_info = tool_call.get("function", {})
                    tool_name = function_info.get("name", "")
                    
                    if tool_name in ["prompt_service_set_prompt", "prompt_service_delete_prompt"]:
                        self.logger.debug(f"检测到提示词工具调用: {tool_name}, chat_id={chat_id}")
                        
        except Exception as e:
            self.logger.error(f"处理工具操作失败: {e}")
            
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
            
    async def _process_tool_calls(self, session_id: str, chat_id: str, 
                                initial_response: Dict[str, Any], 
                                session_data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.tool_manager:
            return initial_response
            
        current_response = initial_response
        call_count = 0
        
        while call_count < self.max_tool_calls:
            if not await self._has_tool_calls(current_response):
                break
                
            tool_calls = await self._extract_tool_calls(current_response)
            if not tool_calls:
                break
                
            tool_results = []
            for tool_call in tool_calls:
                tool_result = await self._execute_tool_call(
                    tool_call=tool_call,
                    session_id=session_id,
                    chat_id=chat_id
                )
                
                if tool_result:
                    tool_results.append(tool_result)
                    
            if not tool_results:
                break
                
            if self.session_manager:
                await self.session_manager.update_session(session_id=session_id, tool_results=tool_results)
                session_result = await self.session_manager.get_session(session_id)
                if session_result.get("success"):
                    session_data = session_result.get("data")
                    
            current_response = await self._call_model_service(session_data, chat_id)
            if not current_response:
                break
                
            call_count += 1
            
        return current_response
        
    async def _has_tool_calls(self, response: Dict[str, Any]) -> bool:
        if not response:
            return False
            
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls", [])
            return len(tool_calls) > 0
            
        if "tool_calls" in response:
            return len(response["tool_calls"]) > 0
            
        return False
        
    async def _extract_tool_calls(self, response: Dict[str, Any]) -> list:
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
                
        elif "tool_calls" in response:
            for tool_call in response["tool_calls"]:
                tool_calls.append(tool_call)
                
        return tool_calls
        
    async def _execute_tool_call(self, tool_call: Dict[str, Any], 
                               session_id: str, chat_id: str) -> Optional[Dict[str, Any]]:
        if not self.tool_manager:
            return None
            
        try:
            function_info = tool_call.get("function", {})
            tool_name = function_info.get("name")
            arguments_str = function_info.get("arguments", "{}")
            
            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                arguments = {}
            
            if "chat_id" not in arguments and chat_id:
                arguments["chat_id"] = chat_id
                
            result = await self.tool_manager.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                session_id=session_id,
                chat_id=chat_id
            )
            
            if result and result.get("success") and self.context_manager:
                action = result.get("action", "")
                
                if action == "set_prompt" and "prompt_content" in result:
                    update_result = await self.context_manager.update_custom_prompt(
                        chat_id=chat_id,
                        prompt_content=result.get("prompt_content", "")
                    )
                elif action == "delete_prompt":
                    delete_result = await self.context_manager.delete_custom_prompt(chat_id)
                elif action == "view_prompt":
                    get_result = await self.context_manager.get_custom_prompt(chat_id)
                    if get_result.get("success"):
                        has_custom = get_result.get("has_custom_prompt", False)
                        result.update({
                            "has_custom_prompt": has_custom,
                            "custom_prompt": get_result.get("custom_prompt", ""),
                            "core_prompt": get_result.get("core_prompt", "")
                        })
            
            return {
                "tool_call_id": tool_call.get("id"),
                "role": "tool",
                "name": tool_name,
                "content": json.dumps(result) if result else "{}"
            }
            
        except Exception as e:
            self.logger.error(f"执行工具调用失败: {e}")
            return None
            
    async def _create_error_result(self, task_info: Dict[str, Any], error_msg: str) -> Dict[str, Any]:
        return {
            "workflow_type": task_info.get("workflow_type", "unknown"),
            "task_id": task_info.get("task_id"),
            "chat_id": task_info.get("task_data", {}).get("chat_id"),
            "success": False,
            "error": error_msg
        }