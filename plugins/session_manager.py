#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
session_manager.py - 异步会话临时缓存管理器
基于asyncio的完全异步会话管理系统
增加了图片处理功能
"""

import uuid
import logging
import asyncio
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class SessionData:
    """会话数据结构"""
    session_id: str
    chat_id: str
    created_at: float
    last_updated: float
    data: Dict[str, Any]
    tool_call_count: int = 0


class SessionManager:
    """异步会话管理器"""
    
    def __init__(self):
        """初始化异步会话管理器"""
        self.logger = logging.getLogger(__name__)
        
        # 会话存储
        self.sessions = {}  # session_id -> SessionData
        
        # 聊天ID到会话ID的映射
        self.chat_to_sessions = {}  # chat_id -> List[session_id]
        
        # 配置
        self.config = None
        
        # 锁（异步）
        self.lock = asyncio.Lock()
        
        # 清理守护任务
        self.cleanup_task = None
        self.is_running = False
        
        # 会话ID生成计数器
        self.session_counter = 0
        
        # 图片管理器引用
        self.image_manager = None
        
    async def initialize(self, config: Dict[str, Any]):
        """异步初始化会话管理器"""
        self.config = config.get("system", {}).get("session_manager", {})
        
        # 获取配置参数
        self.session_timeout_minutes = self.config.get("session_timeout_minutes", 10)
        self.max_sessions = self.config.get("max_sessions", 100)
        
        # 启动清理守护任务
        self.is_running = True
        self.cleanup_task = asyncio.create_task(self._cleanup_daemon())
        
        self.logger.info(f"异步会话管理器初始化完成，最大会话数: {self.max_sessions}")
        
    def set_image_manager(self, image_manager):
        """设置图片管理器引用"""
        self.image_manager = image_manager
        self.logger.info("图片管理器已注入会话管理器")
        
    async def _cleanup_daemon(self):
        """异步清理守护任务"""
        while self.is_running:
            try:
                await asyncio.sleep(60)
                
                current_time = time.time()
                timeout_seconds = self.session_timeout_minutes * 60
                sessions_to_remove = []
                
                async with self.lock:
                    # 检查超时会话
                    for session_id, session_data in list(self.sessions.items()):
                        inactive_time = current_time - session_data.last_updated
                        if inactive_time >= timeout_seconds:
                            sessions_to_remove.append(session_id)
                            
                    # 清理超时会话
                    for session_id in sessions_to_remove:
                        await self._cleanup_session(session_id)
                        
                    # 检查会话数量限制
                    while len(self.sessions) > self.max_sessions:
                        # 找到最久未更新的会话
                        oldest_session = None
                        oldest_time = current_time
                        
                        for session_id, session_data in self.sessions.items():
                            if session_data.last_updated < oldest_time:
                                oldest_time = session_data.last_updated
                                oldest_session = session_id
                                
                        if oldest_session:
                            await self._cleanup_session(oldest_session)
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"会话清理守护任务异常: {e}")
                
    async def _generate_session_id(self, chat_id: str) -> str:
        """异步生成会话ID"""
        async with self.lock:
            self.session_counter += 1
            timestamp = int(time.time())
            unique_id = uuid.uuid4().hex[:8]
            
            return f"sess_{chat_id}_{timestamp}_{self.session_counter}_{unique_id}"
            
    async def _cleanup_session(self, session_id: str):
        """异步清理会话"""
        if session_id not in self.sessions:
            return
            
        try:
            async with self.lock:
                session_data = self.sessions[session_id]
                chat_id = session_data.chat_id
                
                # 从chat_to_sessions映射中移除
                if chat_id in self.chat_to_sessions:
                    if session_id in self.chat_to_sessions[chat_id]:
                        self.chat_to_sessions[chat_id].remove(session_id)
                        
                    # 如果该chat_id没有其他会话，移除映射
                    if not self.chat_to_sessions[chat_id]:
                        del self.chat_to_sessions[chat_id]
                        
                # 从会话存储中移除
                del self.sessions[session_id]
                
            self.logger.debug(f"会话已异步清理: {session_id}")
            
        except Exception as e:
            self.logger.error(f"异步清理会话失败 {session_id}: {e}")
            
    async def create_session(self, chat_id: str, context_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        异步创建临时会话缓存
        
        Args:
            chat_id: 对话ID
            context_data: 上下文数据
            
        Returns:
            创建结果
        """
        try:
            # 过滤和重组上下文数据
            filtered_data = await self._filter_and_reorganize_context(chat_id, context_data)
            if not filtered_data:
                return {
                    "success": False,
                    "error": "无法处理上下文数据"
                }
                
            # 生成会话ID
            session_id = await self._generate_session_id(chat_id)
            
            # 创建会话数据
            session_data = SessionData(
                session_id=session_id,
                chat_id=chat_id,
                created_at=time.time(),
                last_updated=time.time(),
                data=filtered_data,
                tool_call_count=0
            )
            
            # 存储会话
            async with self.lock:
                self.sessions[session_id] = session_data
                
                # 更新chat_to_sessions映射
                if chat_id not in self.chat_to_sessions:
                    self.chat_to_sessions[chat_id] = []
                self.chat_to_sessions[chat_id].append(session_id)
                
            self.logger.debug(f"会话已异步创建: {session_id} for chat_id: {chat_id}")
            
            return {
                "success": True,
                "session_id": session_id,
                "chat_id": chat_id,
                "message": "会话创建成功"
            }
            
        except Exception as e:
            self.logger.error(f"异步创建会话失败 {chat_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def _filter_and_reorganize_context(self, chat_id: str, context_data: Dict[str, Any]) -> Dict[str, Any]:
        """异步过滤和重组上下文数据"""
        try:
            # 提取必要信息
            chat_mode = context_data.get("chat_mode", "LLM")
            tools_call = context_data.get("tools_call", True)
            data_section = context_data.get("data", {})
            
            # 处理消息
            messages = data_section.get("messages", [])
            filtered_messages = []
            
            # 移除虚拟回复
            for message in messages:
                role = message.get("role")
                content = message.get("content", "")
                
                # 如果是助手消息且内容是虚拟回复，跳过
                if role == "assistant" and isinstance(content, str):
                    virtual_reply_text = self.config.get("virtual_reply_text", "已跳过此信息")
                    if content == virtual_reply_text:
                        continue
                        
                # 根据chat_mode处理消息内容
                if chat_mode == "LLM" and role == "user":
                    # LLM模式下，只保留文本内容
                    if isinstance(content, list):
                        text_content = []
                        for item in content:
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    text_content.append(item.get("text", ""))
                                elif item.get("type") == "image_url":
                                    # LLM模式下去除图片信息
                                    continue
                        content = text_content if len(text_content) > 1 else (text_content[0] if text_content else "")
                        
                filtered_messages.append({
                    "role": role,
                    "content": content
                })
                
            # =========== 新增功能：图片处理 ===========
            # 处理用户消息中的图片URL，替换为base64格式
            if self.image_manager:
                processed_messages = []
                
                for message in filtered_messages:
                    if message.get("role") == "user":
                        # 异步处理图片URL
                        processed_content = await self.image_manager.replace_urls_with_base64(
                            chat_id=chat_id,
                            messages=[message]  # 单个消息包装成列表
                        )
                        
                        if processed_content and len(processed_content) > 0:
                            processed_message = message.copy()
                            processed_message["content"] = processed_content[0].get("content", message["content"])
                            processed_messages.append(processed_message)
                        else:
                            processed_messages.append(message)
                    else:
                        processed_messages.append(message)
                        
                filtered_messages = processed_messages
                
            self.logger.debug(f"已处理消息中的图片URL，消息数: {len(filtered_messages)}")
            # ===========================================
            
            # 构建过滤后的数据
            filtered_data = {
                "model": data_section.get("model", "local_model"),
                "messages": filtered_messages,
                "max_tokens": data_section.get("max_tokens", 64000),
                "temperature": data_section.get("temperature", 0.7),
                "stream": data_section.get("stream", False)
            }
            
            # 根据tools_call决定是否包含工具定义
            if tools_call and "tools" in data_section:
                filtered_data["tools"] = data_section["tools"]
                
            return filtered_data
            
        except Exception as e:
            self.logger.error(f"异步过滤上下文数据失败 {chat_id}: {e}")
            return {}
            
    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """
        异步获取会话数据
        
        Args:
            session_id: 会话ID
            
        Returns:
            会话数据
        """
        if session_id not in self.sessions:
            return {
                "success": False,
                "error": f"会话不存在: {session_id}"
            }
            
        try:
            async with self.lock:
                session_data = self.sessions[session_id]
                
                # 更新最后访问时间
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
            return {
                "success": False,
                "error": str(e)
            }
            
    async def update_session(self, session_id: str, tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        异步更新会话数据
        
        Args:
            session_id: 会话ID
            tool_results: 工具执行结果列表
            
        Returns:
            更新结果
        """
        if session_id not in self.sessions:
            return {
                "success": False,
                "error": f"会话不存在: {session_id}"
            }
            
        try:
            async with self.lock:
                session_data = self.sessions[session_id]
                
                # 添加工具执行结果到消息列表
                for tool_result in tool_results:
                    if isinstance(tool_result, dict):
                        session_data.data["messages"].append(tool_result)
                        
                # 更新工具调用计数
                session_data.tool_call_count += len(tool_results)
                
                # 更新最后更新时间
                session_data.last_updated = time.time()
                
                self.logger.debug(f"会话已异步更新: {session_id}, 工具调用次数: {session_data.tool_call_count}")
                
                return {
                    "success": True,
                    "message": f"会话已更新，新增 {len(tool_results)} 个工具结果",
                    "tool_call_count": session_data.tool_call_count
                }
                
        except Exception as e:
            self.logger.error(f"异步更新会话数据失败 {session_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def cleanup_session(self, session_id: str) -> Dict[str, Any]:
        """
        异步清理会话缓存
        
        Args:
            session_id: 会话ID
            
        Returns:
            清理结果
        """
        if session_id not in self.sessions:
            return {
                "success": False,
                "error": f"会话不存在: {session_id}"
            }
            
        try:
            # 清理会话
            await self._cleanup_session(session_id)
            
            return {
                "success": True,
                "message": "会话已清理"
            }
            
        except Exception as e:
            self.logger.error(f"异步清理会话失败 {session_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def get_sessions_by_chat_id(self, chat_id: str) -> List[str]:
        """异步获取指定对话的所有会话ID"""
        async with self.lock:
            return self.chat_to_sessions.get(chat_id, [])
            
    async def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """异步获取会话信息"""
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
        """异步获取所有会话信息"""
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
        """异步获取状态信息"""
        async with self.lock:
            status = {
                "total_sessions": len(self.sessions),
                "max_sessions": self.max_sessions,
                "session_timeout_minutes": self.session_timeout_minutes,
                "sessions_by_chat": {
                    chat_id: len(sessions)
                    for chat_id, sessions in self.chat_to_sessions.items()
                },
                "has_image_manager": self.image_manager is not None
            }
            
        return status
        
    async def shutdown(self):
        """关闭异步会话管理器"""
        self.is_running = False
        
        # 等待清理任务结束
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
                
        # 清理所有会话
        async with self.lock:
            session_ids = list(self.sessions.keys())
            for session_id in session_ids:
                await self._cleanup_session(session_id)
                
        self.logger.info("异步会话管理器已关闭")