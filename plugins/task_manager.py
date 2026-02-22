import asyncio
import logging
import time
import json
import re

class ToolCallTask:
    def __init__(self, tool_call_id, session_id, chat_id, tool_name, arguments):
        self.tool_call_id = tool_call_id
        self.session_id = session_id
        self.chat_id = chat_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.status = "pending"
        self.start_time = 0
        self.result = None

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
        
        self.tool_tracker = {}
        self.session_semaphores = {}
        self.max_tool_calls = 10
        
        self.think_filters = [
            (r'<think>.*?</think>', 'remove'),
            (r'<\|thinking\|>.*?</\|thinking\|>', 'remove'),
            (r'\[思考\].*?\[/思考\]', 'remove'),
            (r'</think>', 'after'),
            (r'</\|thinking\|>', 'after'),
            (r'\[/思考\]', 'after'),
        ]
        
    async def initialize(self, config, **kwargs):
        self.config = config
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                
        if self.session_manager:
            await self.session_manager.register_cleanup_callback(self._on_session_cleanup)
        
    async def _on_session_cleanup(self, session_id):
        await self.cleanup_session_tools(session_id)
        if session_id in self.session_semaphores:
            del self.session_semaphores[session_id]
        if session_id in self.tool_tracker:
            del self.tool_tracker[session_id]
    
    def set_message_callback(self, callback):
        self.message_callback = callback
        
    async def execute_task(self, task_info):
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
            
    async def _workflow_a(self, task_info):
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
        
    async def _workflow_b(self, task_info):
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
        
    async def _workflow_c(self, task_info):
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
                
            if await self._has_tool_calls(model_response):
                final_response = await self._handle_tool_calls(
                    session_id=session_id,
                    chat_id=chat_id,
                    model_response=model_response,
                    session_data=session_data
                )
            else:
                final_response = model_response
                
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
            
    async def _handle_tool_calls(self, session_id, chat_id, model_response, session_data):
        if not self.tool_manager or not self.session_manager:
            return model_response
            
        tool_calls = await self._extract_tool_calls(model_response)
        if not tool_calls:
            return model_response
            
        assistant_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls
        }
        await self.session_manager.add_tool_call_message(session_id, assistant_message)
        
        tool_results = await self._execute_tools_for_session(
            session_id=session_id,
            chat_id=chat_id,
            tool_calls=tool_calls
        )
        
        await self.session_manager.add_tool_results(session_id, tool_results)
        
        session_result = await self.session_manager.get_session(session_id)
        if session_result.get("success"):
            updated_session_data = session_result.get("data")
        else:
            return model_response
            
        return await self._call_model_service(updated_session_data, chat_id)
        
    async def _execute_tools_for_session(self, session_id, chat_id, tool_calls):
        if session_id not in self.session_semaphores:
            self.session_semaphores[session_id] = asyncio.Semaphore(1)
            
        async with self.session_semaphores[session_id]:
            return await self._execute_tools_serial(tool_calls, chat_id, session_id)
            
    async def _execute_tools_serial(self, tool_calls, chat_id, session_id):
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
        
    async def _execute_single_tool(self, tool_call, chat_id, session_id):
        try:
            tool_call_id = tool_call.get("id")
            function_info = tool_call.get("function", {})
            tool_name = function_info.get("name")
            arguments_str = function_info.get("arguments", "{}")
            
            try:
                arguments = json.loads(arguments_str)
            except json.JSONDecodeError:
                arguments = {}
                
            task = ToolCallTask(
                tool_call_id=tool_call_id,
                session_id=session_id,
                chat_id=chat_id,
                tool_name=tool_name,
                arguments=arguments
            )
            
            self._track_tool_call(task)
            
            try:
                result = await self.tool_manager.execute_tool_with_timeout(
                    tool_name=tool_name,
                    arguments=arguments,
                    chat_id=chat_id,
                    session_id=session_id
                )
                
                content = result
                    
                task.status = "completed"
                task.result = content
                
            except asyncio.TimeoutError:
                content = "工具执行超时"
                task.status = "timeout"
                task.result = content
            except Exception as e:
                content = f"工具执行失败: {str(e)}"
                task.status = "failed"
                task.result = content
                
            finally:
                self._update_tool_call(task)
                
            return {
                "tool_call_id": tool_call_id,
                "role": "tool",
                "name": tool_name,
                "content": content
            }
            
        except Exception as e:
            self.logger.error(f"执行工具调用失败: {e}")
            return None
            
    def _track_tool_call(self, task):
        if task.session_id not in self.tool_tracker:
            self.tool_tracker[task.session_id] = {}
            
        self.tool_tracker[task.session_id][task.tool_call_id] = task
        
    def _update_tool_call(self, task):
        if (task.session_id in self.tool_tracker and 
            task.tool_call_id in self.tool_tracker[task.session_id]):
            self.tool_tracker[task.session_id][task.tool_call_id] = task
            
    async def _has_tool_calls(self, response):
        if not response:
            return False
            
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            tool_calls = message.get("tool_calls", [])
            return len(tool_calls) > 0
            
        return False
        
    async def _extract_tool_calls(self, response):
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
        
    async def _call_model_service(self, session_data, chat_id):
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
            self.logger.error(f"调用模型服务失败: {e}")
            return None
    
    def _filter_thinking(self, text):
        if not isinstance(text, str):
            return text
    
        # 1. 尝试删除完整的思考块（remove模式）
        for pattern, mode in self.think_filters:
            if mode == 'remove':
                cleaned = re.sub(pattern, '', text, flags=re.DOTALL)
                if len(cleaned) < len(text):
                    return cleaned.strip()
    
        # 2. 尝试查找结束标记，取之后的内容（after模式）
        for pattern, mode in self.think_filters:
            if mode == 'after':
                match = re.search(pattern, text, flags=re.DOTALL)
                if match:
                    after = text[match.end():]
                    if after.strip():
                        return after.strip()
    
        # 3. 没有匹配任何思考规则，返回原文本（不做任何截断）
        return text.strip()
            
    async def _extract_response_content(self, model_response):
        if not model_response:
            return "模型服务返回空响应"
        try:
            if "choices" in model_response and model_response["choices"]:
                choice = model_response["choices"][0]
                if "message" in choice:
                    message = choice["message"]
                    if "content" in message and message["content"]:
                        raw_content = message["content"]
                        if isinstance(raw_content, list):
                            return raw_content
                        filtered = self._filter_thinking(raw_content)
                        return filtered
                    elif "tool_calls" in message:
                        return "[抱歉，群聊太过抽象，响应失败啦]"
            if "content" in model_response:
                raw_content = model_response["content"]
                if isinstance(raw_content, list):
                    return raw_content
                return self._filter_thinking(raw_content)
            return str(model_response)
        except Exception as e:
            self.logger.error(f"提取响应内容失败: {e}")
            return "无法解析模型响应"
            
    async def _create_error_result(self, task_info, error_msg):
        return {
            "workflow_type": task_info.get("workflow_type", "unknown"),
            "task_id": task_info.get("task_id"),
            "chat_id": task_info.get("task_data", {}).get("chat_id"),
            "success": False,
            "error": error_msg
        }
        
    async def cleanup_session_tools(self, session_id):
        if session_id == "*":
            self.tool_tracker.clear()
            self.session_semaphores.clear()
        elif session_id in self.tool_tracker:
            del self.tool_tracker[session_id]
        if session_id in self.session_semaphores:
            del self.session_semaphores[session_id]
            
    async def get_tool_tracking_status(self):
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
                    "status": task.status,
                    "tool_name": task.tool_name,
                    "running_time": time.time() - task.start_time if task.start_time > 0 else 0
                }
                
            status["sessions"][session_id] = session_status
            
        return status