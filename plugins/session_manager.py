#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
session_manager.py - 优化后的异步会话临时缓存管理器
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
            filtered_messages = []
            
            # 找到最后一个用户消息的索引和所有工具消息
            last_user_msg_index = -1
            tool_messages = []
            for i, message in enumerate(messages):
                role = message.get("role")
                if role == "user":
                    last_user_msg_index = i
                elif role == "tool":
                    tool_messages.append((i, message))
            
            has_tool_messages = len(tool_messages) > 0
            
            # 处理所有消息
            for i, message in enumerate(messages):
                role = message.get("role")
                content = message.get("content", "")
                
                # 检查是否是当前问题的虚拟回复
                is_current_virtual_reply = False
                if role == "assistant" and isinstance(content, str):
                    virtual_reply_text = self.config.get("virtual_reply_text", "已跳过此信息")
                    if content == virtual_reply_text:
                        # 检查这个虚拟回复是否是紧跟在最后一个用户消息之后的
                        if i == last_user_msg_index + 1:
                            # 这是当前问题的虚拟回复，应该移除
                            is_current_virtual_reply = True
                        else:
                            # 这是其他虚拟回复，应该保留
                            filtered_messages.append({"role": role, "content": content})
                            continue
                
                # 如果已经处理了虚拟回复（保留或跳过），继续下一个消息
                if role == "assistant" and isinstance(content, str) and is_current_virtual_reply:
                    # 当前问题的虚拟回复被跳过，不添加到filtered_messages
                    continue
                
                if role == "user":
                    # 检查是否是最后一个用户消息（即当前问题）
                    is_current_question = (i == last_user_msg_index)
                    
                    if chat_mode == "LLM":
                        if isinstance(content, list):
                            # 处理多模态消息
                            processed_content = []
                            has_text = False
                            text_items = []
                            
                            for item in content:
                                if isinstance(item, dict):
                                    item_type = item.get("type")
                                    if item_type == "text":
                                        text = item.get("text", "")
                                        if isinstance(text, str) and text.strip():
                                            text_items.append(text)
                                            has_text = True
                                    elif item_type == "image_url":
                                        # 图片部分保持原样
                                        processed_content.append(item)
                            
                            # 处理文本部分
                            if has_text and is_current_question and not has_tool_messages:
                                # 当前问题且没有工具消息：为文本添加注意力集中前缀
                                combined_text = " ".join(text_items) if len(text_items) > 1 else text_items[0] if text_items else ""
                                formatted_text = f"【当前用户请求】请专注回答以下问题：\n{combined_text}"
                                processed_content.insert(0, {"type": "text", "text": formatted_text})
                                filtered_messages.append({"role": role, "content": processed_content})
                            else:
                                # 已经有工具消息或不是当前问题：保持原始格式
                                if has_text:
                                    if len(text_items) == 1:
                                        processed_content.insert(0, {"type": "text", "text": text_items[0]})
                                    else:
                                        for text in text_items:
                                            processed_content.append({"type": "text", "text": text})
                                filtered_messages.append({"role": role, "content": processed_content})
                        elif isinstance(content, str):
                            # 纯文本消息
                            if is_current_question and not has_tool_messages:
                                # 当前问题且没有工具消息：添加注意力集中前缀
                                formatted_content = f"【当前用户请求】请专注回答以下问题：\n{content}"
                                filtered_messages.append({"role": role, "content": formatted_content})
                            else:
                                # 已经有工具消息或不是当前问题：保持原始格式
                                filtered_messages.append({"role": role, "content": content})
                    else:
                        # MLLM模式：也为当前问题的文本添加注意力前缀（如果没有工具消息）
                        if isinstance(content, list):
                            processed_content = []
                            has_text = False
                            text_items = []
                            
                            for item in content:
                                if isinstance(item, dict):
                                    item_type = item.get("type")
                                    if item_type == "text":
                                        text = item.get("text", "")
                                        if isinstance(text, str) and text.strip():
                                            text_items.append(text)
                                            has_text = True
                                    elif item_type == "image_url":
                                        processed_content.append(item)
                            
                            if has_text and is_current_question and not has_tool_messages:
                                combined_text = " ".join(text_items) if len(text_items) > 1 else text_items[0] if text_items else ""
                                formatted_text = f"【当前用户请求】请专注回答以下问题：\n{combined_text}"
                                processed_content.insert(0, {"type": "text", "text": formatted_text})
                                filtered_messages.append({"role": role, "content": processed_content})
                            else:
                                if has_text:
                                    if len(text_items) == 1:
                                        processed_content.insert(0, {"type": "text", "text": text_items[0]})
                                    else:
                                        for text in text_items:
                                            processed_content.append({"type": "text", "text": text})
                                filtered_messages.append({"role": role, "content": processed_content})
                        elif isinstance(content, str):
                            if is_current_question and not has_tool_messages:
                                formatted_content = f"【当前用户请求】请专注回答以下问题：\n{content}"
                                filtered_messages.append({"role": role, "content": formatted_content})
                            else:
                                filtered_messages.append({"role": role, "content": content})
                        
                elif role == "tool":
                    # 工具调用结果：智能处理多轮调用
                    if isinstance(content, str):
                        try:
                            import json
                            content_data = json.loads(content)
                            if isinstance(content_data, dict):
                                result_content = content_data.get("result", content_data.get("content", str(content_data)))
                            else:
                                result_content = content_data
                        except:
                            result_content = content
                        
                        # 检查这是第几个工具消息
                        current_tool_index = len([msg for msg in filtered_messages if msg.get("role") == "tool"])
                        
                        if current_tool_index == 0:
                            # 第一个工具结果：添加整理指令
                            formatted_content = f"请整理当前工具执行的返回结果：{result_content}"
                            
                            # 恢复之前的用户消息格式（如果已添加前缀）
                            for j in range(len(filtered_messages)-1, -1, -1):
                                prev_msg = filtered_messages[j]
                                if prev_msg.get("role") == "user":
                                    prev_content = prev_msg.get("content", "")
                                    if isinstance(prev_content, str) and prev_content.startswith("【当前用户请求】"):
                                        # 移除注意力集中前缀，恢复原始格式
                                        original_content = prev_content.replace("【当前用户请求】请专注回答以下问题：\n", "")
                                        filtered_messages[j] = {"role": "user", "content": original_content}
                                        break
                                    elif isinstance(prev_content, list):
                                        for k, item in enumerate(prev_content):
                                            if isinstance(item, dict) and item.get("type") == "text":
                                                text = item.get("text", "")
                                                if isinstance(text, str) and text.startswith("【当前用户请求】"):
                                                    original_text = text.replace("【当前用户请求】请专注回答以下问题：\n", "")
                                                    prev_content[k] = {"type": "text", "text": original_text}
                                                    filtered_messages[j] = {"role": "user", "content": prev_content}
                                                    break
                        else:
                            # 后续工具结果：移除之前工具结果的指令，只添加最新指令
                            for j in range(len(filtered_messages)-1, -1, -1):
                                prev_msg = filtered_messages[j]
                                if prev_msg.get("role") == "tool":
                                    prev_content = prev_msg.get("content", "")
                                    if isinstance(prev_content, str) and prev_content.startswith("请整理当前工具执行的返回结果："):
                                        clean_content = prev_content.replace("请整理当前工具执行的返回结果：", "", 1)
                                        filtered_messages[j] = {"role": "tool", "content": clean_content}
                                        break
                            
                            formatted_content = f"请整理当前工具执行的返回结果：{result_content}"
                        
                        filtered_messages.append({"role": role, "content": formatted_content})
                    else:
                        filtered_messages.append({"role": role, "content": content})
                        
                elif role == "assistant" and not is_current_virtual_reply:
                    # 处理非虚拟回复的assistant消息
                    filtered_messages.append({"role": role, "content": content})
            
            # 图片处理
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
                    "tool_call_count": session_data.tool_call_count
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