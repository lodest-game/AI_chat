#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task_manager.py - 异步任务调度器
完全异步的工作流执行引擎
"""

import asyncio
import logging
import time
import json
from typing import Dict, Any, Optional, Callable
import traceback


class TaskManager:
    """异步任务调度器"""
    
    def __init__(self):
        """初始化异步任务调度器"""
        self.logger = logging.getLogger(__name__)
        
        # 模块引用
        self.context_manager = None
        self.session_manager = None
        self.essentials_manager = None
        self.tool_manager = None
        self.port_manager = None
        
        # 消息回调
        self.message_callback = None
        
        # 配置
        self.config = None
        
        # 工具调用循环限制
        self.max_tool_calls = 10
        
    async def initialize(self, config: Dict[str, Any], **kwargs):
        """异步初始化任务调度器"""
        self.config = config
        
        # 设置模块引用
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                
        self.logger.info("异步任务调度器初始化完成")
        
    def set_message_callback(self, callback: Callable):
        """设置消息回调函数"""
        self.message_callback = callback
        
    async def execute_task(self, task_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        异步执行任务
        
        Args:
            task_info: 任务信息
            
        Returns:
            任务执行结果
        """
        try:
            workflow_type = task_info.get("workflow_type", "A")
            
            if workflow_type == "A":
                return await self._workflow_a(task_info)
            elif workflow_type == "B":
                return await self._workflow_b(task_info)
            elif workflow_type == "C":
                return await self._workflow_c(task_info)
            else:
                self.logger.error(f"未知的工作流类型: {workflow_type}")
                return await self._create_error_result(task_info, f"未知工作流类型: {workflow_type}")
                
        except Exception as e:
            self.logger.error(f"任务执行失败: {e}")
            self.logger.error(traceback.format_exc())
            return await self._create_error_result(task_info, str(e))
            
    async def _workflow_a(self, task_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        工作流A：非模型响应处理流程
        
        处理普通用户消息，添加虚拟回复
        """
        task_data = task_info.get("task_data", {})
        chat_id = task_data.get("chat_id")
        
        self.logger.info(f"执行异步工作流A: chat_id={chat_id}")
        
        # 1. 异步更新上下文缓存数据
        if self.context_manager:
            try:
                # 更新上下文，添加虚拟回复
                context_result = await self.context_manager.update_context(
                    chat_id=chat_id,
                    message_data=task_data
                )
                
                if not context_result.get("success"):
                    self.logger.warning(f"更新上下文失败: {context_result.get('error')}")
                    
            except Exception as e:
                self.logger.error(f"更新上下文异常: {e}")
                
        # 2. 检查是否是指令
        is_command = False
        command_result = None
        
        if self.essentials_manager:
            is_command = self.essentials_manager.is_command(task_data)
            if is_command:
                # 执行指令
                command_result = await self.essentials_manager.execute_command(task_data)
                
        # 3. 构建返回结果
        result = {
            "workflow_type": "A",
            "task_id": task_info.get("task_id"),
            "chat_id": chat_id,
            "is_command": is_command,
            "success": True
        }
        
        if is_command and command_result:
            # 确保指令结果包含正确的格式
            if "content" not in command_result:
                command_result["content"] = "指令执行成功"
            if "chat_id" not in command_result:
                command_result["chat_id"] = chat_id
                
            result["response"] = command_result
            
        self.logger.info(f"异步工作流A执行完成: chat_id={chat_id}")
        
        return result
        
    async def _workflow_b(self, task_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        工作流B：模型响应预处理流程
        
        为AI处理准备数据，创建临时会话
        """
        task_data = task_info.get("task_data", {})
        chat_id = task_data.get("chat_id")
        
        self.logger.info(f"执行异步工作流B: chat_id={chat_id}")
        
        # ========== 检测指令 ==========
        # 在进入工作流B之前，检查是否是指令
        is_command = False
        command_result = None
        
        if self.essentials_manager:
            is_command = self.essentials_manager.is_command(task_data)
            if is_command:
                self.logger.info(f"检测到指令消息，转入工作流A处理: chat_id={chat_id}")
                
                # 执行指令
                command_result = await self.essentials_manager.execute_command(task_data)
                
                # 构建返回结果（模拟工作流A的结果格式）
                result = {
                    "workflow_type": "A",  # 标记为工作流A的结果
                    "task_id": task_info.get("task_id"),
                    "chat_id": chat_id,
                    "is_command": True,
                    "success": True
                }
                
                if command_result:
                    # 确保指令结果包含正确的格式
                    if "content" not in command_result:
                        command_result["content"] = "指令执行成功"
                    if "chat_id" not in command_result:
                        command_result["chat_id"] = chat_id
                        
                    result["response"] = command_result
                    
                # 立即返回指令结果，不继续工作流B的后续处理
                self.logger.info(f"指令处理完成，返回结果: chat_id={chat_id}")
                return result
        # ===================================
        
        # 工作流B逻辑（仅当不是指令时执行）
        # 1. 异步更新上下文缓存数据
        context_data = None
        if self.context_manager:
            try:
                # 更新上下文，添加虚拟回复
                context_result = await self.context_manager.update_context(
                    chat_id=chat_id,
                    message_data=task_data
                )
                
                if context_result.get("success"):
                    context_data = context_result.get("data")
                else:
                    self.logger.warning(f"更新上下文失败: {context_result.get('error')}")
                    
            except Exception as e:
                self.logger.error(f"更新上下文异常: {e}")
                
        # 2. 创建临时会话缓存
        session_id = None
        if self.session_manager and context_data:
            try:
                # 创建会话
                session_result = await self.session_manager.create_session(
                    chat_id=chat_id,
                    context_data=context_data
                )
                
                if session_result.get("success"):
                    session_id = session_result.get("session_id")
                    self.logger.debug(f"创建会话成功: session_id={session_id}")
                else:
                    self.logger.warning(f"创建会话失败: {session_result.get('error')}")
                    
            except Exception as e:
                self.logger.error(f"创建会话异常: {e}")
                
        # 3. 构建返回结果
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
            
        self.logger.info(f"异步工作流B执行完成: chat_id={chat_id}, session_id={session_id}")
        
        return result
        
    async def _workflow_c(self, task_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        工作流C：模型处理流程
        
        调用AI模型，处理工具调用循环
        """
        task_data = task_info.get("task_data", {})
        session_id = task_data.get("session_id")
        chat_id = task_data.get("chat_id")
        
        self.logger.info(f"执行异步工作流C: chat_id={chat_id}, session_id={session_id}")
        
        if not session_id or not self.session_manager:
            return await self._create_error_result(
                task_info, 
                "缺少session_id或session_manager未初始化"
            )
            
        try:
            # 1. 获取会话数据
            session_result = await self.session_manager.get_session(session_id)
            if not session_result.get("success"):
                return await self._create_error_result(
                    task_info,
                    f"获取会话失败: {session_result.get('error')}"
                )
                
            session_data = session_result.get("data")
            
            # 2. 异步发送到模型服务
            model_response = await self._call_model_service(session_data, chat_id)
            
            if not model_response:
                # 记录详细的错误信息
                self.logger.error(f"模型服务调用失败: chat_id={chat_id}")
                return await self._create_error_result(task_info, "模型服务调用失败")
                
            # 3. 异步处理工具调用循环
            final_response = await self._process_tool_calls(
                session_id=session_id,
                chat_id=chat_id,
                initial_response=model_response,
                session_data=session_data
            )
            
            # 4. 检查是否有需要处理的工具操作结果
            await self._handle_tool_operations(final_response, chat_id)
            
            # 5. 清理会话缓存
            await self.session_manager.cleanup_session(session_id)
            
            # 6. 提取模型回复内容
            response_content = await self._extract_response_content(final_response)
            
            # 7. 构建返回结果
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
            
            self.logger.info(f"异步工作流C执行完成: chat_id={chat_id}, session_id={session_id}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"工作流C执行异常: {e}")
            self.logger.exception(e)
            
            # 清理会话缓存
            if self.session_manager and session_id:
                await self.session_manager.cleanup_session(session_id)
                
            return await self._create_error_result(task_info, str(e))
            
    async def _handle_tool_operations(self, model_response: Dict[str, Any], chat_id: str):
        """异步处理工具操作结果"""
        if not model_response or not self.context_manager:
            return
            
        try:
            # 检查是否是工具调用结果
            if await self._has_tool_calls(model_response):
                tool_calls = await self._extract_tool_calls(model_response)
                for tool_call in tool_calls:
                    function_info = tool_call.get("function", {})
                    tool_name = function_info.get("name", "")
                    
                    # 检查是否是提示词相关的工具调用
                    if tool_name in ["prompt_service_set_prompt", "prompt_service_delete_prompt"]:
                        self.logger.info(f"检测到提示词工具调用: {tool_name}, chat_id={chat_id}")
                        
        except Exception as e:
            self.logger.error(f"处理工具操作失败: {e}")
            
    async def _extract_response_content(self, model_response: Dict[str, Any]) -> str:
        """从模型响应中提取回复内容"""
        if not model_response:
            return "模型服务返回空响应"
            
        try:
            # OpenAI API格式
            if "choices" in model_response and model_response["choices"]:
                choice = model_response["choices"][0]
                if "message" in choice:
                    message = choice["message"]
                    if "content" in message and message["content"]:
                        return message["content"]
                    elif "tool_calls" in message:
                        return "[模型请求工具调用]"
                        
            # 其他格式
            if "content" in model_response:
                return model_response["content"]
                
            # 默认返回字符串表示
            return str(model_response)
            
        except Exception as e:
            self.logger.error(f"提取响应内容失败: {e}")
            return "无法解析模型响应"
            
    async def _call_model_service(self, session_data: Dict[str, Any], chat_id: str) -> Optional[Dict[str, Any]]:
        """异步调用模型服务"""
        if not self.port_manager:
            self.logger.error("port_manager未初始化")
            return None
            
        try:
            # 准备请求数据
            request_data = {
                "chat_id": chat_id,
                "session_data": session_data,
                "timestamp": time.time()
            }
            
            # 异步发送请求到模型服务
            response = await self.port_manager.send_to_model_async(request_data)
            
            return response
            
        except Exception as e:
            self.logger.error(f"异步调用模型服务失败: {e}")
            return None
            
    async def _process_tool_calls(self, session_id: str, chat_id: str, 
                                initial_response: Dict[str, Any], 
                                session_data: Dict[str, Any]) -> Dict[str, Any]:
        """异步处理工具调用循环"""
        if not self.tool_manager:
            return initial_response
            
        current_response = initial_response
        call_count = 0
        
        # 最大工具调用次数
        while call_count < self.max_tool_calls:
            # 检查是否包含工具调用
            if not await self._has_tool_calls(current_response):
                break
                
            # 提取工具调用信息
            tool_calls = await self._extract_tool_calls(current_response)
            if not tool_calls:
                break
                
            # 执行工具调用
            tool_results = []
            for tool_call in tool_calls:
                tool_result = await self._execute_tool_call(
                    tool_call=tool_call,
                    session_id=session_id,
                    chat_id=chat_id
                )
                
                if tool_result:
                    tool_results.append(tool_result)
                    
            # 如果没有工具执行结果，跳出循环
            if not tool_results:
                break
                
            # 更新会话数据
            if self.session_manager:
                update_result = await self.session_manager.update_session(
                    session_id=session_id,
                    tool_results=tool_results
                )
                
                if not update_result.get("success"):
                    self.logger.warning(f"更新会话失败: {update_result.get('error')}")
                    
                # 获取更新后的会话数据
                session_result = await self.session_manager.get_session(session_id)
                if session_result.get("success"):
                    session_data = session_result.get("data")
                    
            # 再次调用模型服务
            current_response = await self._call_model_service(session_data, chat_id)
            if not current_response:
                break
                
            call_count += 1
            
        # 记录工具调用次数
        if call_count > 0:
            self.logger.info(f"工具调用循环完成: chat_id={chat_id}, 调用次数={call_count}")
            
        return current_response
        
    async def _has_tool_calls(self, response: Dict[str, Any]) -> bool:
        """检查响应是否包含工具调用"""
        if not response:
            return False
            
        # OpenAI格式的工具调用检查
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls", [])
            return len(tool_calls) > 0
            
        # 自定义格式检查
        if "tool_calls" in response:
            return len(response["tool_calls"]) > 0
            
        return False
        
    async def _extract_tool_calls(self, response: Dict[str, Any]) -> list:
        """提取工具调用信息"""
        tool_calls = []
        
        # OpenAI格式
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
                
        # 自定义格式
        elif "tool_calls" in response:
            for tool_call in response["tool_calls"]:
                tool_calls.append(tool_call)
                
        return tool_calls
        
    async def _execute_tool_call(self, tool_call: Dict[str, Any], 
                               session_id: str, chat_id: str) -> Optional[Dict[str, Any]]:
        """异步执行工具调用"""
        if not self.tool_manager:
            return None
            
        try:
            # 提取函数信息
            function_info = tool_call.get("function", {})
            tool_name = function_info.get("name")
            arguments_str = function_info.get("arguments", "{}")
            
            # 解析参数
            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                self.logger.error(f"工具参数解析失败: {arguments_str}")
                arguments = {}
            
            # 添加chat_id到参数中
            if "chat_id" not in arguments and chat_id:
                arguments["chat_id"] = chat_id
                
            # 执行工具
            result = await self.tool_manager.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                session_id=session_id,
                chat_id=chat_id
            )
            
            # 检查是否是提示词相关工具，如果是则需要更新上下文
            if result and result.get("success") and self.context_manager:
                action = result.get("action", "")
                
                if action == "set_prompt" and "prompt_content" in result:
                    # 设置专属提示词
                    update_result = await self.context_manager.update_custom_prompt(
                        chat_id=chat_id,
                        prompt_content=result.get("prompt_content", "")
                    )
                    if update_result.get("success"):
                        self.logger.info(f"提示词已通过工具更新: {chat_id}")
                    else:
                        self.logger.error(f"提示词更新失败: {update_result.get('error')}")
                        
                elif action == "delete_prompt":
                    # 删除专属提示词 - 调用专用函数
                    delete_result = await self.context_manager.delete_custom_prompt(chat_id)
                    if delete_result.get("success"):
                        self.logger.info(f"提示词已通过工具删除: {chat_id}")
                    else:
                        self.logger.error(f"提示词删除失败: {delete_result.get('error')}")
                        
                elif action == "view_prompt":
                    # 获取专属提示词
                    get_result = await self.context_manager.get_custom_prompt(chat_id)
                    if get_result.get("success"):
                        # 将获取结果合并到工具结果中
                        has_custom = get_result.get("has_custom_prompt", False)
                        result.update({
                            "has_custom_prompt": has_custom,
                            "custom_prompt": get_result.get("custom_prompt", ""),
                            "core_prompt": get_result.get("core_prompt", "")
                        })
                        if has_custom:
                            result["message"] = f"专属提示词: {get_result.get('custom_prompt')}"
                        else:
                            result["message"] = "未设置专属提示词，使用默认核心提示词"
                    else:
                        result["success"] = False
                        result["message"] = f"获取提示词失败: {get_result.get('error')}"
            
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
        """创建错误结果"""
        return {
            "workflow_type": task_info.get("workflow_type", "unknown"),
            "task_id": task_info.get("task_id"),
            "chat_id": task_info.get("task_data", {}).get("chat_id"),
            "success": False,
            "error": error_msg
        }
        
    async def shutdown(self):
        """关闭异步任务调度器"""
        self.logger.info("异步任务调度器已关闭")