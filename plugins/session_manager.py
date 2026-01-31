#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
session_manager.py - 优化后的异步会话临时缓存管理器
支持工具调用消息保存和完整的上下文管理
"""

import uuid
import logging
import asyncio
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class SessionData:
    session_id: str
    chat_id: str
    created_at: float
    last_updated: float
    data: Dict[str, Any]
    tool_call_count: int = 0


class SessionManager:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.sessions = {}
        self.chat_to_sessions = {}
        self.config = None
        self.lock = asyncio.Lock()
        self.cleanup_task = None
        self.is_running = False
        self.session_counter = 0
        self.image_manager = None
        
    async def initialize(self, config: Dict[str, Any]):
        self.config = config.get("system", {}).get("session_manager", {})
        self.session_timeout_minutes = self.config.get("session_timeout_minutes", 10)
        self.max_sessions = self.config.get("max_sessions", 100)
        self.is_running = True
        self.cleanup_task = asyncio.create_task(self._cleanup_daemon())
        
    def set_image_manager(self, image_manager):
        self.image_manager = image_manager
        
    async def _cleanup_daemon(self):
        while self.is_running:
            await asyncio.sleep(60)
            current_time = time.time()
            timeout_seconds = self.session_timeout_minutes * 60
            sessions_to_remove = []
            
            async with self.lock:
                for session_id, session_data in list(self.sessions.items()):
                    inactive_time = current_time - session_data.last_updated
                    if inactive_time >= timeout_seconds:
                        sessions_to_remove.append(session_id)
                        
                for session_id in sessions_to_remove:
                    await self._cleanup_session(session_id)
                    
                while len(self.sessions) > self.max_sessions:
                    oldest_session = None
                    oldest_time = current_time
                    
                    for session_id, session_data in self.sessions.items():
                        if session_data.last_updated < oldest_time:
                            oldest_time = session_data.last_updated
                            oldest_session = session_id
                            
                    if oldest_session:
                        await self._cleanup_session(oldest_session)
                            
    async def _generate_session_id(self, chat_id: str) -> str:
        async with self.lock:
            self.session_counter += 1
            timestamp = int(time.time())
            unique_id = uuid.uuid4().hex[:8]
            return f"sess_{chat_id}_{timestamp}_{self.session_counter}_{unique_id}"
            
    async def _cleanup_session(self, session_id: str):
        if session_id not in self.sessions:
            return
            
        try:
            async with self.lock:
                session_data = self.sessions[session_id]
                chat_id = session_data.chat_id
                
                if chat_id in self.chat_to_sessions:
                    if session_id in self.chat_to_sessions[chat_id]:
                        self.chat_to_sessions[chat_id].remove(session_id)
                        
                    if not self.chat_to_sessions[chat_id]:
                        del self.chat_to_sessions[chat_id]
                        
                del self.sessions[session_id]
                
        except Exception as e:
            self.logger.error(f"异步清理会话失败 {session_id}: {e}")
            
    async def create_session(self, chat_id: str, context_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            filtered_data = await self._filter_and_reorganize_context(chat_id, context_data)
            if not filtered_data:
                return {"success": False, "error": "无法处理上下文数据"}
                
            session_id = await self._generate_session_id(chat_id)
            
            session_data = SessionData(
                session_id=session_id,
                chat_id=chat_id,
                created_at=time.time(),
                last_updated=time.time(),
                data=filtered_data,
                tool_call_count=0
            )
            
            async with self.lock:
                self.sessions[session_id] = session_data
                
                if chat_id not in self.chat_to_sessions:
                    self.chat_to_sessions[chat_id] = []
                self.chat_to_sessions[chat_id].append(session_id)
                
            return {
                "success": True,
                "session_id": session_id,
                "chat_id": chat_id,
                "message": "会话创建成功"
            }
            
        except Exception as e:
            self.logger.error(f"异步创建会话失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def _filter_and_reorganize_context(self, chat_id: str, context_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            chat_mode = context_data.get("chat_mode", "LLM")
            tools_call = context_data.get("tools_call", True)
            data_section = context_data.get("data", {})
            messages = data_section.get("messages", [])
            
            # 创建一个深拷贝，避免修改原始数据
            import copy
            filtered_messages = copy.deepcopy(messages)
            
            # 查找当前请求的用户消息（最后一条用户消息）
            last_user_message_index = -1
            for i in range(len(filtered_messages) - 1, -1, -1):
                if filtered_messages[i].get("role") == "user":
                    last_user_message_index = i
                    break
            
            # 处理所有消息
            for i, message in enumerate(filtered_messages):
                role = message.get("role")
                content = message.get("content", "")
                
                # LLM模式下的用户消息处理
                if chat_mode == "LLM" and role == "user":
                    if isinstance(content, list):
                        text_content = []
                        for item in content:
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    text_content.append(item.get("text", ""))
                                elif item.get("type") == "image_url":
                                    continue
                        # 简化处理：如果是多部分内容，只取文本部分
                        if text_content:
                            content = " ".join(text_content) if len(text_content) > 1 else text_content[0]
                        else:
                            content = ""
                    
                    # 只对当前请求的用户消息（最后一条用户消息）添加注意力前缀
                    if i == last_user_message_index:
                        # 确保content是字符串
                        if not isinstance(content, str):
                            content = str(content)
                        
                        # 移除可能已经存在的前缀（避免重复添加）
                        # 如果内容以"当前请求："开头，说明已经添加过前缀，直接使用
                        if content.startswith("当前请求："):
                            # 已经添加过前缀，无需再次添加
                            pass
                        else:
                            # 添加注意力前缀
                            content = f"当前请求：\n{content}\n\n注意：以上是当前需要处理的具体问题，请优先关注并回应当前请求。历史对话仅作为背景信息参考。"
                    else:
                        # 对于历史用户消息，移除可能存在的注意力前缀
                        # 确保content是字符串
                        if not isinstance(content, str):
                            content = str(content)
                        
                        # 移除注意力前缀
                        if content.startswith("当前请求：\n"):
                            # 查找"注意："的位置
                            attention_pos = content.find("\n\n注意：")
                            if attention_pos != -1:
                                # 提取实际内容（移除前缀）
                                # 跳过"当前请求：\n"（6个字符）到"\n\n注意："之间的部分
                                # 需要解析出实际内容
                                lines = content.split('\n')
                                if len(lines) >= 2 and lines[0] == "当前请求：":
                                    # 第二行开始是实际内容，直到遇到空行后跟"注意："
                                    actual_content_lines = []
                                    in_content = False
                                    for line in lines[1:]:
                                        if line.startswith("注意："):
                                            break
                                        if line == "" and not actual_content_lines:
                                            continue  # 跳过空行
                                        actual_content_lines.append(line)
                                    content = '\n'.join(actual_content_lines)
                    
                    # 更新消息内容
                    message["content"] = content
                    
                elif role == "user" and isinstance(content, list):
                    # 处理MLLM模式或多模态消息
                    # 只对当前请求的用户消息添加前缀
                    if i == last_user_message_index:
                        # 检查是否已经添加了前缀
                        has_prefix = False
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text = item.get("text", "")
                                if text.startswith("当前请求："):
                                    has_prefix = True
                                    break
                        
                        # 如果没有前缀，添加到第一个文本元素
                        if not has_prefix:
                            for j, item in enumerate(content):
                                if isinstance(item, dict) and item.get("type") == "text":
                                    current_text = item.get("text", "")
                                    # 添加注意力前缀
                                    content[j]["text"] = f"当前请求：\n{current_text}\n\n注意：以上是当前需要处理的具体问题，请优先关注并回应当前请求。历史对话仅作为背景信息参考。"
                                    break
            
            if self.image_manager:
                processed_messages = await self._process_images_in_messages(chat_id, filtered_messages)
                filtered_messages = processed_messages
                
            filtered_data = {
                "model": data_section.get("model", "local_model"),
                "messages": filtered_messages,
                "max_tokens": data_section.get("max_tokens", 64000),
                "temperature": data_section.get("temperature", 0.1),
                "stream": data_section.get("stream", False)
            }
            
            if tools_call and "tools" in data_section:
                filtered_data["tools"] = data_section["tools"]
                
            return filtered_data
            
        except Exception as e:
            self.logger.error(f"异步过滤上下文数据失败 {chat_id}: {e}")
            return {}
            
    async def _process_images_in_messages(self, chat_id: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """优化后的图片处理逻辑，直接查询图片状态并处理"""
        processed_messages = []
        
        for message in messages:
            if not isinstance(message, dict):
                processed_messages.append(message)
                continue
                
            role = message.get("role")
            content = message.get("content", "")
            
            if role != "user":
                processed_messages.append(message)
                continue
                
            if isinstance(content, str):
                processed_messages.append(message)
                continue
                    
            elif isinstance(content, list):
                new_content = []
                
                for item in content:
                    if isinstance(item, dict):
                        item_type = item.get("type")
                        
                        if item_type == "text":
                            new_content.append(item)
                            
                        elif item_type == "image_url":
                            image_url = item.get("image_url", {})
                            if isinstance(image_url, dict):
                                url = image_url.get("url", "")
                            elif isinstance(image_url, str):
                                url = image_url
                            else:
                                url = ""
                                
                            if url and url.startswith(("http://", "https://")):
                                # 简化：直接查询图片状态并处理
                                result = await self._handle_image_url(chat_id, url)
                                if result:
                                    new_content.append({
                                        "type": "image_url",
                                        "image_url": {"url": result}
                                    })
                                else:
                                    self.logger.debug(f"图片URL无法处理，已移除: {url[:50]}...")
                            elif url and url.startswith("data:image/"):
                                new_content.append(item)
                            else:
                                continue
                        else:
                            new_content.append(item)
                    else:
                        new_content.append(item)
                        
                if new_content:
                    processed_message = message.copy()
                    processed_message["content"] = new_content
                    processed_messages.append(processed_message)
                else:
                    processed_messages.append(message)
            else:
                processed_messages.append(message)
                
        return processed_messages
        
    async def _handle_image_url(self, chat_id: str, url: str) -> Optional[str]:
        """简化的图片URL处理逻辑"""
        try:
            # 1. 先尝试从缓存获取
            base64_data = await self.image_manager.get_image_base64(chat_id, url)
            if base64_data:
                return base64_data
                
            # 2. 检查是否正在处理中
            async with self.image_manager.lock:
                is_processing = url in self.image_manager.processing_tasks
                
            if is_processing:
                # 等待处理完成
                try:
                    # 等待现有任务完成
                    task = self.image_manager.processing_tasks.get(url)
                    if task:
                        result = await task
                        if result.get("success"):
                            # 任务完成后再次尝试获取
                            base64_data = await self.image_manager.get_image_base64(chat_id, url)
                            return base64_data
                except Exception as e:
                    self.logger.error(f"等待图片处理任务失败: {e}")
                    
            # 3. 既不在缓存也不在处理的，移除（返回None）
            return None
            
        except Exception as e:
            self.logger.error(f"处理图片URL失败: {e}")
            return None
            
    async def add_tool_call_message(self, session_id: str, assistant_message: Dict[str, Any]) -> Dict[str, Any]:
        """添加AI的工具调用消息到会话"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"会话不存在: {session_id}"}
            
        try:
            async with self.lock:
                session_data = self.sessions[session_id]
                
                # 添加assistant的tool_calls消息
                session_data.data["messages"].append(assistant_message)
                session_data.last_updated = time.time()
                
            return {"success": True, "message": "工具调用消息已添加到会话"}
            
        except Exception as e:
            self.logger.error(f"添加工具调用消息失败 {session_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def add_tool_results(self, session_id: str, tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """添加工具执行结果到会话"""
        if session_id not in self.sessions:
            return {"success": False, "error": f"会话不存在: {session_id}"}
            
        try:
            async with self.lock:
                session_data = self.sessions[session_id]
                
                for tool_result in tool_results:
                    if isinstance(tool_result, dict):
                        session_data.data["messages"].append(tool_result)
                        
                session_data.tool_call_count += len(tool_results)
                session_data.last_updated = time.time()
                
            return {
                "success": True,
                "message": f"工具结果已添加到会话，新增 {len(tool_results)} 个结果",
                "tool_call_count": session_data.tool_call_count
            }
            
        except Exception as e:
            self.logger.error(f"添加工具结果失败 {session_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def get_session(self, session_id: str) -> Dict[str, Any]:
        if session_id not in self.sessions:
            return {"success": False, "error": f"会话不存在: {session_id}"}
            
        try:
            async with self.lock:
                session_data = self.sessions[session_id]
                session_data.last_updated = time.time()
                
                return {
                    "success": True,
                    "data": session_data.data,
                    "session_id": session_id,
                    "chat_id": session_data.chat_id,
                    "tool_call_count": session_data.tool_call_count
                }
                
        except Exception as e:
            self.logger.error(f"异步获取会话数据失败 {session_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def update_session(self, session_id: str, tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """更新会话数据（兼容旧接口）"""
        return await self.add_tool_results(session_id, tool_results)
            
    async def cleanup_session(self, session_id: str) -> Dict[str, Any]:
        if session_id not in self.sessions:
            return {"success": False, "error": f"会话不存在: {session_id}"}
            
        try:
            await self._cleanup_session(session_id)
            return {"success": True, "message": "会话已清理"}
            
        except Exception as e:
            self.logger.error(f"异步清理会话失败 {session_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def get_sessions_by_chat_id(self, chat_id: str) -> List[str]:
        async with self.lock:
            return self.chat_to_sessions.get(chat_id, [])
            
    async def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        if session_id not in self.sessions:
            return None
            
        session_data = self.sessions[session_id]
        
        return {
            "session_id": session_id,
            "chat_id": session_data.chat_id,
            "created_at": session_data.created_at,
            "last_updated": session_data.last_updated,
            "tool_call_count": session_data.tool_call_count,
            "age_seconds": time.time() - session_data.created_at,
            "inactive_seconds": time.time() - session_data.last_updated
        }
        
    async def get_all_sessions_info(self) -> List[Dict[str, Any]]:
        async with self.lock:
            sessions_info = []
            
            for session_id, session_data in self.sessions.items():
                sessions_info.append({
                    "session_id": session_id,
                    "chat_id": session_data.chat_id,
                    "created_at": session_data.created_at,
                    "last_updated": session_data.last_updated,
                    "tool_call_count": session_data.tool_call_count,
                    "age_seconds": time.time() - session_data.created_at,
                    "inactive_seconds": time.time() - session_data.last_updated
                })
                
            return sessions_info
            
    async def get_status(self) -> Dict[str, Any]:
        async with self.lock:
            return {
                "total_sessions": len(self.sessions),
                "max_sessions": self.max_sessions,
                "session_timeout_minutes": self.session_timeout_minutes,
                "sessions_by_chat": {
                    chat_id: len(sessions)
                    for chat_id, sessions in self.chat_to_sessions.items()
                },
                "has_image_manager": self.image_manager is not None
            }
        
    async def shutdown(self):
        self.is_running = False
        
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
                
        async with self.lock:
            session_ids = list(self.sessions.keys())
            for session_id in session_ids:
                await self._cleanup_session(session_id)