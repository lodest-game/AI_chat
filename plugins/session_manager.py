#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
session_manager.py - 优化后的异步会话临时缓存管理器
总结请求时只在临时会话中禁用工具调用，不影响原始上下文
"""

import uuid
import logging
import asyncio
import time
import re
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
    is_summary_request: bool = False


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
        
        self.summary_keywords = [
            "总结", "概括", "summarize", "summary", "概述", "归纳", "评价",
            "总结一下", "概括一下", "做个总结", "来个总结", "总结讨论",
            "总结对话", "总结聊天", "总结以上", "总结刚才", "总结内容",
            "总结一下讨论", "总结这段对话", "总结聊天记录"
        ]
        
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
            
    async def _is_summary_request(self, context_data: Dict[str, Any]) -> bool:
        """检测是否为总结请求"""
        try:
            if not context_data or "data" not in context_data:
                return False
                
            data_section = context_data.get("data", {})
            messages = data_section.get("messages", [])
            
            if not messages:
                return False
                
            # 只检查最新的用户消息
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    
                    # 处理纯文本消息
                    if isinstance(content, str):
                        content_lower = content.lower()
                        for keyword in self.summary_keywords:
                            if keyword in content_lower:
                                self.logger.info(f"检测到总结请求: {content[:50]}...")
                                return True
                                
                    # 处理多模态消息（文本+图片）
                    elif isinstance(content, list):
                        text_parts = []
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text = item.get("text", "")
                                if isinstance(text, str):
                                    text_parts.append(text)
                                    
                        if text_parts:
                            combined_text = " ".join(text_parts).lower()
                            for keyword in self.summary_keywords:
                                if keyword in combined_text:
                                    self.logger.info(f"检测到多模态总结请求: {combined_text[:50]}...")
                                    return True
                    break  # 只检查最新的用户消息
                    
            return False
            
        except Exception as e:
            self.logger.error(f"检测总结请求失败: {e}")
            return False
            
    async def create_session(self, chat_id: str, context_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            # 检测是否为总结请求
            is_summary_request = await self._is_summary_request(context_data)
            
            # 过滤和重组上下文，传递总结请求标记
            filtered_data = await self._filter_and_reorganize_context(
                chat_id=chat_id,
                context_data=context_data,
                is_summary_request=is_summary_request
            )
            
            session_id = await self._generate_session_id(chat_id)
            
            session_data = SessionData(
                session_id=session_id,
                chat_id=chat_id,
                created_at=time.time(),
                last_updated=time.time(),
                data=filtered_data,
                tool_call_count=0,
                is_summary_request=is_summary_request
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
                "is_summary_request": is_summary_request,
                "message": "会话创建成功"
            }
            
        except Exception as e:
            self.logger.error(f"异步创建会话失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def _filter_and_reorganize_context(self, chat_id: str, context_data: Dict[str, Any], 
                                           is_summary_request: bool = False) -> Dict[str, Any]:
        """过滤和重组上下文，is_summary_request控制是否移除工具定义"""
        try:
            chat_mode = context_data.get("chat_mode", "LLM")
            tools_call = context_data.get("tools_call", True)
            data_section = context_data.get("data", {})
            messages = data_section.get("messages", [])
            filtered_messages = []
            
            for message in messages:
                role = message.get("role")
                content = message.get("content", "")
                
                if role == "assistant" and isinstance(content, str):
                    virtual_reply_text = self.config.get("virtual_reply_text", "已跳过此信息")
                    if content == virtual_reply_text:
                        continue
                        
                if chat_mode == "LLM" and role == "user":
                    if isinstance(content, list):
                        text_content = []
                        for item in content:
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    text_content.append(item.get("text", ""))
                                elif item.get("type") == "image_url":
                                    continue
                        content = text_content if len(text_content) > 1 else (text_content[0] if text_content else "")
                        
                filtered_messages.append({"role": role, "content": content})
                
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
            
            # 关键修改：只有在不是总结请求时才添加工具定义
            if tools_call and "tools" in data_section and not is_summary_request:
                filtered_data["tools"] = data_section["tools"]
            else:
                # 对于总结请求，确保没有工具定义
                # 不设置 "tools" 键即可
                pass
                
            # 如果是总结请求，增强系统提示词
            if is_summary_request and filtered_messages:
                for i, msg in enumerate(filtered_messages):
                    if msg.get("role") == "system":
                        original_content = msg.get("content", "")
                        # 添加明确的总结指令
                        summary_instruction = (
                            "\n\n【当前为总结请求】"
                            "\n- 请直接总结对话内容，不要调用任何工具"
                            "\n- 保持简洁自然，不需要询问或确认"
                        )
                        filtered_messages[i]["content"] = original_content + summary_instruction
                        break
                        
                self.logger.info(f"总结请求会话创建 - chat_id: {chat_id}, 已移除工具定义")
                        
            return filtered_data
            
        except Exception as e:
            self.logger.error(f"异步过滤上下文数据失败 {chat_id}: {e}")
            return {}
            
    async def _process_images_in_messages(self, chat_id: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """优化后的图片处理逻辑"""
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
        """
        简化的图片URL处理逻辑
        根据ImageManager的状态进行相应处理
        """
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
                    "tool_call_count": session_data.tool_call_count,
                    "is_summary_request": session_data.is_summary_request
                }
                
        except Exception as e:
            self.logger.error(f"异步获取会话数据失败 {session_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def update_session(self, session_id: str, tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        if session_id not in self.sessions:
            return {"success": False, "error": f"会话不存在: {session_id}"}
            
        try:
            async with self.lock:
                session_data = self.sessions[session_id]
                
                # 如果是总结请求，拒绝添加工具结果
                if session_data.is_summary_request and tool_results:
                    self.logger.warning(f"总结请求中阻止工具调用: {session_id}")
                    return {
                        "success": True,
                        "message": "总结请求中不处理工具调用",
                        "tool_call_count": session_data.tool_call_count
                    }
                
                # 正常添加工具结果
                for tool_result in tool_results:
                    if isinstance(tool_result, dict):
                        session_data.data["messages"].append(tool_result)
                        
                session_data.tool_call_count += len(tool_results)
                session_data.last_updated = time.time()
                
                return {
                    "success": True,
                    "message": f"会话已更新，新增 {len(tool_results)} 个工具结果",
                    "tool_call_count": session_data.tool_call_count
                }
                
        except Exception as e:
            self.logger.error(f"异步更新会话数据失败 {session_id}: {e}")
            return {"success": False, "error": str(e)}
            
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
            "is_summary_request": session_data.is_summary_request,
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
                    "is_summary_request": session_data.is_summary_request,
                    "age_seconds": time.time() - session_data.created_at,
                    "inactive_seconds": time.time() - session_data.last_updated
                })
                
            return sessions_info
            
    async def get_status(self) -> Dict[str, Any]:
        async with self.lock:
            summary_count = sum(1 for s in self.sessions.values() if s.is_summary_request)
            
            return {
                "total_sessions": len(self.sessions),
                "summary_sessions": summary_count,
                "max_sessions": self.max_sessions,
                "session_timeout_minutes": self.session_timeout_minutes,
                "sessions_by_chat": {
                    chat_id: len(sessions)
                    for chat_id, sessions in self.chat_to_sessions.items()
                },
                "has_image_manager": self.image_manager is not None,
                "summary_keywords": self.summary_keywords
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