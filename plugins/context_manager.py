#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context_manager.py - 异步上下文管理器
基于asyncio的完全异步上下文管理系统
修复对话轮次完整性，确保删除用户消息时同时删除相关的AI回复
"""

import os
import json
import logging
import asyncio
import time
import hashlib
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple


class ContextManager:
    """异步上下文管理器"""
    
    def __init__(self, history_dir: Path):
        """
        初始化异步上下文管理器
        
        Args:
            history_dir: 历史记录目录
        """
        self.logger = logging.getLogger(__name__)
        
        # 目录配置
        self.history_dir = Path(history_dir)
        
        # 上下文缓存（每个对话独立）
        self.context_cache = {}  # chat_id -> context_data
        self.cache_status = {}  # chat_id -> {"last_access": timestamp, "is_dirty": bool}
        
        # 配置
        self.config = None
        
        # 锁（异步）
        self.lock = asyncio.Lock()
        
        # 清理守护任务
        self.cleanup_task = None
        self.is_running = False
        
        # 默认配置
        self.default_config = {
            "default_model": "local_model",
            "chat_mode": {"LLM": ["local_model"], "MLLM": []},
            "default_tools_call": True,
            "model": {
                "max_tokens": 64000,
                "temperature": 0.7,
                "stream": False
            },
            "core_prompt": ["你是智能助手"],
            "max_user_messages_per_chat": 20,
            "virtual_reply_enabled": True,
            "virtual_reply_text": "已跳过此信息",
            "cache_inactive_unload_seconds": 1800
        }
        
        # 工具管理器引用
        self.tool_manager = None
        
    async def initialize(self, config: Dict[str, Any]):
        """异步初始化上下文管理器"""
        self.config = config.get("system", {}).get("context_manager", self.default_config)
        
        # 确保目录存在
        self.history_dir.mkdir(exist_ok=True)
        
        # 从配置中获取参数
        self.max_user_messages_per_chat = self.config.get("max_user_messages_per_chat", 20)
        self.virtual_reply_enabled = self.config.get("virtual_reply_enabled", True)
        self.virtual_reply_text = self.config.get("virtual_reply_text", "已跳过此信息")
        self.cache_inactive_unload_seconds = self.config.get("cache_inactive_unload_seconds", 1800)
        
        # 获取核心提示词
        self.core_prompt = self.config.get("core_prompt", ["你是智能助手"])
        if isinstance(self.core_prompt, list):
            self.core_prompt = "\n".join(self.core_prompt)
            
        # 启动清理守护任务
        self.is_running = True
        self.cleanup_task = asyncio.create_task(self._cleanup_daemon())
        
        self.logger.info(f"异步上下文管理器初始化完成，每个对话最大用户消息数: {self.max_user_messages_per_chat}")
        
    async def _cleanup_daemon(self):
        """异步清理守护任务 - 专注于检测不活跃对话"""
        while self.is_running:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次
                
                current_time = time.time()
                to_unload = []
                
                async with self.lock:
                    # 只检测不活跃的缓存
                    for chat_id, status in list(self.cache_status.items()):
                        inactive_time = current_time - status["last_access"]
                        if inactive_time >= self.cache_inactive_unload_seconds:
                            to_unload.append(chat_id)
                
                # 异步卸载检测到的不活跃对话
                for chat_id in to_unload:
                    # 先保存再卸载（避免数据丢失）
                    await self._save_context_if_dirty(chat_id)
                    await self._remove_from_cache(chat_id)
                    self.logger.debug(f"已卸载不活跃对话: {chat_id}")
                            
            except Exception as e:
                self.logger.error(f"清理守护任务异常: {e}")
                
    def _get_context_file_path(self, chat_id: str) -> Path:
        """获取上下文文件路径"""
        # 安全处理文件名
        safe_chat_id = re.sub(r'[<>:"/\\|?*]', '_', str(chat_id))
        
        # 限制文件名长度
        if len(safe_chat_id) > 200:
            hash_part = hashlib.md5(chat_id.encode()).hexdigest()[:8]
            safe_chat_id = f"{safe_chat_id[:150]}_{hash_part}"
        
        return self.history_dir / f"{safe_chat_id}.json"
        
    async def _load_context_from_file(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """从文件异步加载上下文"""
        file_path = self._get_context_file_path(chat_id)
        
        if not file_path.exists():
            return None
            
        try:
            # 异步读取文件
            loop = asyncio.get_event_loop()
            with open(file_path, 'r', encoding='utf-8') as f:
                context_data = await loop.run_in_executor(None, json.load, f)
                
            self.logger.debug(f"从文件异步加载上下文: {chat_id}")
            return context_data
            
        except Exception as e:
            self.logger.error(f"异步加载上下文文件失败 {chat_id}: {e}")
            return None
            
    async def _save_context_to_file(self, chat_id: str):
        """异步保存上下文到文件"""
        if chat_id not in self.context_cache:
            return
            
        context_data = self.context_cache.get(chat_id)
        if not context_data:
            return
            
        file_path = self._get_context_file_path(chat_id)
        
        try:
            # 异步写入文件
            loop = asyncio.get_event_loop()
            
            # 确保目录存在
            file_path.parent.mkdir(exist_ok=True)
            
            # 保存到文件
            with open(file_path, 'w', encoding='utf-8') as f:
                await loop.run_in_executor(
                    None, 
                    lambda: json.dump(context_data, f, ensure_ascii=False, indent=2)
                )
                
            # 更新脏数据状态
            async with self.lock:
                if chat_id in self.cache_status:
                    self.cache_status[chat_id]["is_dirty"] = False
                
            self.logger.debug(f"上下文已异步保存到文件: {chat_id}")
            
        except Exception as e:
            self.logger.error(f"异步保存上下文文件失败 {chat_id}: {e}")
            
    async def _save_context_if_dirty(self, chat_id: str):
        """如果缓存是脏的，保存到文件"""
        if chat_id not in self.cache_status:
            return
            
        if self.cache_status[chat_id].get("is_dirty", False):
            await self._save_context_to_file(chat_id)
            
    async def _remove_from_cache(self, chat_id: str):
        """从缓存中移除对话（不保存）"""
        async with self.lock:
            if chat_id in self.context_cache:
                del self.context_cache[chat_id]
                
            if chat_id in self.cache_status:
                del self.cache_status[chat_id]
                
    def _ensure_default_context(self, chat_id: str) -> Dict[str, Any]:
        """创建默认上下文"""
        # 获取聊天模式
        chat_mode = self._determine_chat_mode(chat_id)
        
        # 构建默认上下文
        context_data = {
            "chat_id": chat_id,
            "chat_mode": chat_mode,
            "tools_call": self.config.get("default_tools_call", True),
            "data": {
                "model": self.config.get("default_model", "local_model"),
                "messages": [
                    {
                        "role": "system",
                        "content": self.core_prompt
                    }
                ],
                "max_tokens": self.config.get("model", {}).get("max_tokens", 64000),
                "temperature": self.config.get("model", {}).get("temperature", 0.7),
                "stream": self.config.get("model", {}).get("stream", False)
            }
        }
        
        # 如果工具管理器可用，添加工具定义
        if self.tool_manager:
            tools_definition = self.tool_manager.get_tool_definitions()
            context_data["data"]["tools"] = tools_definition
        
        return context_data
        
    def _determine_chat_mode(self, chat_id: str) -> str:
        """确定聊天模式"""
        chat_mode_config = self.config.get("chat_mode", {})
        
        if chat_mode_config.get("LLM"):
            return "LLM"
        elif chat_mode_config.get("MLLM"):
            return "MLLM"
        else:
            return "LLM"
            
    def set_tool_manager(self, tool_manager):
        """设置工具管理器引用"""
        self.tool_manager = tool_manager
        self.logger.info("工具管理器已注入异步上下文管理器")
    
    async def _sync_tools_to_context(self, chat_id: str, context_data: Dict[str, Any]):
        """异步同步工具定义到上下文"""
        if not self.tool_manager:
            return
            
        try:
            # 获取最新的工具定义
            current_tools = self.tool_manager.get_tool_definitions()
            
            # 更新上下文中的工具定义
            if "data" in context_data:
                context_data["data"]["tools"] = current_tools
                self.logger.debug(f"异步同步工具定义到上下文: {chat_id}, 工具数: {len(current_tools)}")
                
        except Exception as e:
            self.logger.error(f"异步同步工具定义失败 {chat_id}: {e}")
    
    async def get_context(self, chat_id: str) -> Dict[str, Any]:
        """异步获取对话上下文"""
        try:
            async with self.lock:
                # 检查是否已缓存
                if chat_id in self.context_cache:
                    # 更新访问时间
                    if chat_id in self.cache_status:
                        self.cache_status[chat_id]["last_access"] = time.time()
                        
                    return {
                        "success": True,
                        "data": self.context_cache[chat_id],
                        "from_cache": True
                    }
                    
                # 从文件加载
                context_data = await self._load_context_from_file(chat_id)
                
                if context_data:
                    # 同步最新的工具定义
                    await self._sync_tools_to_context(chat_id, context_data)
                    
                    # 存入缓存
                    self.context_cache[chat_id] = context_data
                    self.cache_status[chat_id] = {
                        "last_access": time.time(),
                        "is_dirty": False
                    }
                    
                    return {
                        "success": True,
                        "data": context_data,
                        "from_cache": False,
                        "from_file": True
                    }
                    
                # 创建新的上下文
                context_data = self._ensure_default_context(chat_id)
                
                # 存入缓存
                self.context_cache[chat_id] = context_data
                self.cache_status[chat_id] = {
                    "last_access": time.time(),
                    "is_dirty": True
                }
                
                return {
                    "success": True,
                    "data": context_data,
                    "from_cache": False,
                    "from_file": False,
                    "is_new": True
                }
                
        except Exception as e:
            self.logger.error(f"异步获取上下文失败 {chat_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def _trim_context_messages(self, context_data: Dict[str, Any]):
        """
        修剪上下文消息，保持用户消息不超过限制
        确保删除user消息时，同时删除所有相关的assistant回复
        
        对话轮次结构：
        1. system消息（始终保留）
        2. 对话轮次1: user消息 + 0个或多个assistant消息
        3. 对话轮次2: user消息 + 0个或多个assistant消息
        ...
        
        注意：虚拟回复也是assistant消息
        """
        if "data" not in context_data or "messages" not in context_data["data"]:
            return
            
        messages = context_data["data"]["messages"]
        if not messages:
            return
            
        # 统计用户消息数量
        user_message_count = 0
        for message in messages:
            if message.get("role") == "user":
                user_message_count += 1
        
        # 如果不超过限制，直接返回
        if user_message_count <= self.max_user_messages_per_chat:
            return
            
        # 需要删除的用户消息数量
        messages_to_remove = user_message_count - self.max_user_messages_per_chat
        
        self.logger.debug(f"需要删除 {messages_to_remove} 个用户消息以满足限制，当前用户消息数: {user_message_count}")
        
        # 从最早的消息开始，删除完整的对话轮次
        removed_count = 0
        i = 0
        
        while i < len(messages) and removed_count < messages_to_remove:
            role = messages[i].get("role")
            
            # 跳过system消息（始终保留）
            if role == "system":
                i += 1
                continue
                
            # 如果是user消息，删除这个对话轮次
            if role == "user":
                # 这个对话轮次开始的位置
                start_index = i
                
                # 找到这个对话轮次的结束位置
                # 一个对话轮次包括：user消息 + 所有连续的assistant消息
                end_index = i + 1
                while end_index < len(messages) and messages[end_index].get("role") == "assistant":
                    end_index += 1
                
                # 计算这个对话轮次中有多少个user消息（应该是1个）
                user_msgs_in_round = 0
                for j in range(start_index, end_index):
                    if messages[j].get("role") == "user":
                        user_msgs_in_round += 1
                
                # 删除这个对话轮次
                deleted_messages = messages[start_index:end_index]
                del messages[start_index:end_index]
                removed_count += user_msgs_in_round
                
                # 记录删除详情
                deleted_roles = [msg.get("role") for msg in deleted_messages]
                self.logger.debug(
                    f"删除对话轮次: 从索引 {start_index} 到 {end_index-1}，"
                    f"包含 {user_msgs_in_round} 个用户消息，"
                    f"总共 {len(deleted_messages)} 条消息，角色序列: {deleted_roles}"
                )
                
                # i不需要增加，因为删除了消息，所以索引不变
            else:
                # 单独的assistant消息（不应该出现，但处理异常）
                self.logger.warning(
                    f"发现孤立的assistant消息，索引 {i}，"
                    f"内容: {messages[i].get('content', '')[:50]}..."
                )
                del messages[i]
                # i不需要增加，因为删除了消息
        
        # 验证最终的用户消息数量
        final_user_count = sum(1 for msg in messages if msg.get("role") == "user")
        final_total_count = len(messages)
        
        self.logger.info(
            f"修剪完成: 删除了 {removed_count} 个用户消息，"
            f"剩余 {final_user_count} 个用户消息，"
            f"总消息数: {final_total_count}"
        )
            
    async def update_context(self, chat_id: str, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        异步更新对话上下文
        
        Args:
            chat_id: 对话ID
            message_data: 消息数据，使用标准协议字段
            
        Returns:
            更新结果
        """
        try:
            # 获取上下文
            context_result = await self.get_context(chat_id)
            if not context_result.get("success"):
                return context_result
                
            context_data = context_result["data"]
            
            # 判断消息类型（基于角色或消息内容）
            role = message_data.get("role", "user")  # 默认用户消息
            is_ai_message = role == "assistant"
            
            if is_ai_message:
                # 添加AI消息（从message字段获取）
                ai_message = message_data.get("message", {})
                if ai_message.get("role") == "assistant":
                    context_data["data"]["messages"].append(ai_message)
                    self.logger.debug(f"添加AI回复到上下文: {chat_id}")
            else:
                # 用户消息 - 提取内容
                message_content = await self._extract_message_content(message_data)
                if not message_content:
                    return {
                        "success": False,
                        "error": "无法提取消息内容"
                    }
                    
                # 添加用户消息
                user_message = {
                    "role": "user",
                    "content": message_content
                }
                
                context_data["data"]["messages"].append(user_message)
                self.logger.debug(f"添加用户消息到上下文: {chat_id}")
                
                # 如果需要，添加虚拟回复
                if self.virtual_reply_enabled:
                    virtual_reply = {
                        "role": "assistant",
                        "content": self.virtual_reply_text
                    }
                    context_data["data"]["messages"].append(virtual_reply)
                    self.logger.debug(f"添加虚拟回复到上下文: {chat_id}")
            
            # 修剪上下文消息，保持用户消息数量不超过限制
            await self._trim_context_messages(context_data)
            
            # 标记为脏数据
            async with self.lock:
                if chat_id in self.cache_status:
                    self.cache_status[chat_id]["is_dirty"] = True
                    self.cache_status[chat_id]["last_access"] = time.time()
                    
            self.logger.debug(f"上下文已异步更新: {chat_id}, 角色: {role}")
            
            return {
                "success": True,
                "data": context_data
            }
            
        except Exception as e:
            self.logger.error(f"异步更新上下文失败 {chat_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def _extract_message_content(self, message_data: Dict[str, Any]) -> Any:
        """提取消息内容并确保至少有一个文本占位符"""
        if not message_data or "content" not in message_data:
            return None
            
        content = message_data["content"]
        
        # 如果是字符串，直接返回
        if isinstance(content, str):
            return content
            
        # 如果是列表（多模态消息）
        if isinstance(content, list):
            # 如果是空列表，返回占位符
            if not content:
                return "[图片消息]"
                
            # 检查内容类型
            text_parts = []
            has_text = False
            image_count = 0
            images_info = []  # 保存图片信息
            
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type == "text":
                        text_content = item.get("text", "").strip()
                        if text_content:
                            text_parts.append(text_content)
                            has_text = True
                    elif item_type == "image_url":
                        image_count += 1
                        images_info.append(item)  # 保存图片信息
                        
            # 情况分析：
            # 1. 有文本内容：返回原始列表（包含文本和图片）
            if has_text:
                return content
                
            # 2. 只有图片，没有文本：
            elif image_count > 0:
                # 创建一个新的列表，包含文本占位符和原始图片信息
                result = []
                # 添加文本占位符
                if image_count == 1:
                    result.append({
                        "type": "text",
                        "text": "[图片消息]"
                    })
                else:
                    result.append({
                        "type": "text", 
                        "text": f"[{image_count}张图片]"
                    })
                # 保留原始图片信息
                result.extend(images_info)
                return result
                
            # 3. 其他情况：返回通用占位符
            else:
                return "[消息]"
                
        return str(content)
        
    async def update_model(self, chat_id: str, model_name: str) -> Dict[str, Any]:
        """
        异步更新对话使用的模型
        
        Args:
            chat_id: 对话ID
            model_name: 模型名称
            
        Returns:
            更新结果
        """
        try:
            # 卸载当前缓存并保存
            await self._save_context_if_dirty(chat_id)
            await self._remove_from_cache(chat_id)
            
            # 从文件重新加载
            context_data = await self._load_context_from_file(chat_id)
            if not context_data:
                context_data = self._ensure_default_context(chat_id)
            
            # 更新模型
            context_data["data"]["model"] = model_name
            
            # 存入缓存
            self.context_cache[chat_id] = context_data
            self.cache_status[chat_id] = {
                "last_access": time.time(),
                "is_dirty": True
            }
            
            # 异步保存到文件
            await self._save_context_to_file(chat_id)
            
            self.logger.info(f"对话模型已异步更新并保存: {chat_id} -> {model_name}")
            
            return {
                "success": True,
                "message": f"模型已更新为: {model_name}"
            }
            
        except Exception as e:
            self.logger.error(f"异步更新模型失败 {chat_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def update_tools_call(self, chat_id: str, enable: bool) -> Dict[str, Any]:
        """
        异步更新工具调用开关
        
        Args:
            chat_id: 对话ID
            enable: 是否启用工具调用
            
        Returns:
            更新结果
        """
        try:
            # 卸载当前缓存并保存
            await self._save_context_if_dirty(chat_id)
            await self._remove_from_cache(chat_id)
            
            # 从文件重新加载
            context_data = await self._load_context_from_file(chat_id)
            if not context_data:
                context_data = self._ensure_default_context(chat_id)
            
            # 更新工具调用开关
            context_data["tools_call"] = enable
            
            # 存入缓存
            self.context_cache[chat_id] = context_data
            self.cache_status[chat_id] = {
                "last_access": time.time(),
                "is_dirty": True
            }
            
            # 异步保存到文件
            await self._save_context_to_file(chat_id)
            
            status_text = "启用" if enable else "禁用"
            self.logger.info(f"工具调用已{status_text}并异步保存: {chat_id}")
            
            return {
                "success": True,
                "message": f"工具调用已{status_text}"
            }
            
        except Exception as e:
            self.logger.error(f"异步更新工具调用失败 {chat_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def update_custom_prompt(self, chat_id: str, prompt_content: str) -> Dict[str, Any]:
        """
        异步更新专属提示词
        
        Args:
            chat_id: 对话ID
            prompt_content: 提示词内容
            
        Returns:
            更新结果
        """
        try:
            # 卸载当前缓存并保存
            await self._save_context_if_dirty(chat_id)
            await self._remove_from_cache(chat_id)
            
            # 从文件重新加载
            context_data = await self._load_context_from_file(chat_id)
            if not context_data:
                context_data = self._ensure_default_context(chat_id)
            
            # 更新系统消息
            messages = context_data["data"].get("messages", [])
            
            # 查找或创建系统消息
            system_message = None
            system_message_index = -1
            for i, msg in enumerate(messages):
                if msg.get("role") == "system":
                    system_message = msg
                    system_message_index = i
                    break
                    
            if not system_message:
                system_message = {"role": "system", "content": ""}
                messages.insert(0, system_message)
                system_message_index = 0
                
            # 构建新的系统消息内容
            if prompt_content and prompt_content.strip():
                full_prompt = f"{prompt_content.strip()}\n{self.core_prompt}"
            else:
                full_prompt = self.core_prompt
                
            # 更新系统消息内容
            system_message["content"] = full_prompt
            
            # 更新消息列表
            if system_message_index >= 0:
                messages[system_message_index] = system_message
            context_data["data"]["messages"] = messages
            
            # 存入缓存
            self.context_cache[chat_id] = context_data
            self.cache_status[chat_id] = {
                "last_access": time.time(),
                "is_dirty": True
            }
            
            # 异步保存到文件
            await self._save_context_to_file(chat_id)
            
            action_text = "更新" if prompt_content and prompt_content.strip() else "删除"
            self.logger.info(f"专属提示词已{action_text}并异步保存: {chat_id}")
            
            return {
                "success": True,
                "message": f"专属提示词已{action_text}"
            }
            
        except Exception as e:
            self.logger.error(f"异步更新专属提示词失败 {chat_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def delete_custom_prompt(self, chat_id: str) -> Dict[str, Any]:
        """
        异步删除专属提示词
        
        Args:
            chat_id: 对话ID
            
        Returns:
            删除结果
        """
        try:
            # 卸载当前缓存并保存
            await self._save_context_if_dirty(chat_id)
            await self._remove_from_cache(chat_id)
            
            # 从文件重新加载
            context_data = await self._load_context_from_file(chat_id)
            if not context_data:
                context_data = self._ensure_default_context(chat_id)
            
            # 更新系统消息 - 只保留核心提示词
            messages = context_data["data"].get("messages", [])
            
            # 查找或创建系统消息
            system_message = None
            system_message_index = -1
            for i, msg in enumerate(messages):
                if msg.get("role") == "system":
                    system_message = msg
                    system_message_index = i
                    break
                    
            if not system_message:
                system_message = {"role": "system", "content": ""}
                messages.insert(0, system_message)
                system_message_index = 0
                
            # 更新系统消息内容 - 只保留核心提示词
            system_message["content"] = self.core_prompt
            
            # 更新消息列表
            if system_message_index >= 0:
                messages[system_message_index] = system_message
            context_data["data"]["messages"] = messages
            
            # 存入缓存
            self.context_cache[chat_id] = context_data
            self.cache_status[chat_id] = {
                "last_access": time.time(),
                "is_dirty": True
            }
            
            # 异步保存到文件
            await self._save_context_to_file(chat_id)
            
            self.logger.info(f"专属提示词已删除并异步保存: {chat_id}")
            
            return {
                "success": True,
                "message": "专属提示词已删除"
            }
            
        except Exception as e:
            self.logger.error(f"异步删除专属提示词失败 {chat_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_custom_prompt(self, chat_id: str) -> Dict[str, Any]:
        """
        异步获取专属提示词
        
        Args:
            chat_id: 对话ID
            
        Returns:
            获取结果
        """
        try:
            # 获取上下文
            context_result = await self.get_context(chat_id)
            if not context_result.get("success"):
                return {
                    "success": False,
                    "error": context_result.get("error", "获取上下文失败")
                }
                
            context_data = context_result["data"]
            
            # 获取系统消息
            messages = context_data.get("data", {}).get("messages", [])
            custom_prompt = ""
            has_custom_prompt = False
            
            for message in messages:
                if message.get("role") == "system":
                    content = message.get("content", "")
                    # 检查是否包含核心提示词
                    if content == self.core_prompt:
                        # 只有核心提示词，没有专属提示词
                        has_custom_prompt = False
                    elif self.core_prompt in content:
                        # 有专属提示词和核心提示词
                        # 提取专属提示词部分
                        custom_prompt = content.replace(self.core_prompt, "").strip()
                        has_custom_prompt = bool(custom_prompt)
                    break
            
            return {
                "success": True,
                "chat_id": chat_id,
                "has_custom_prompt": has_custom_prompt,
                "custom_prompt": custom_prompt,
                "core_prompt": self.core_prompt
            }
            
        except Exception as e:
            self.logger.error(f"异步获取专属提示词失败 {chat_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def update_tools_definition(self, chat_id: str, tools_definition: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        异步更新工具定义
        
        Args:
            chat_id: 对话ID
            tools_definition: 工具定义列表
            
        Returns:
            更新结果
        """
        try:
            # 卸载当前缓存并保存
            await self._save_context_if_dirty(chat_id)
            await self._remove_from_cache(chat_id)
            
            # 从文件重新加载
            context_data = await self._load_context_from_file(chat_id)
            if not context_data:
                context_data = self._ensure_default_context(chat_id)
            
            # 更新工具定义
            context_data["data"]["tools"] = tools_definition
            
            # 存入缓存
            self.context_cache[chat_id] = context_data
            self.cache_status[chat_id] = {
                "last_access": time.time(),
                "is_dirty": True
            }
            
            # 异步保存到文件
            await self._save_context_to_file(chat_id)
            
            self.logger.debug(f"工具定义已异步更新并保存: {chat_id}, 工具数: {len(tools_definition)}")
            
            return {
                "success": True,
                "message": f"工具定义已更新 ({len(tools_definition)} 个工具)"
            }
            
        except Exception as e:
            self.logger.error(f"异步更新工具定义失败 {chat_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def clear_context(self, chat_id: str) -> Dict[str, Any]:
        """
        异步清理对话上下文
        
        Args:
            chat_id: 对话ID
            
        Returns:
            清理结果
        """
        try:
            # 删除文件
            file_path = self._get_context_file_path(chat_id)
            if file_path.exists():
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, file_path.unlink)
                
            # 从缓存中移除
            async with self.lock:
                if chat_id in self.context_cache:
                    del self.context_cache[chat_id]
                    
                if chat_id in self.cache_status:
                    del self.cache_status[chat_id]
                    
            self.logger.info(f"上下文已异步清理: {chat_id}")
            
            return {
                "success": True,
                "message": "对话上下文已清理"
            }
            
        except Exception as e:
            self.logger.error(f"异步清理上下文失败 {chat_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def get_cache_status(self) -> Dict[str, Any]:
        """异步获取缓存状态"""
        async with self.lock:
            status = {
                "total_cached": len(self.context_cache),
                "max_user_messages_per_chat": self.max_user_messages_per_chat,
                "cached_chats": list(self.context_cache.keys()),
                "cache_status": {
                    chat_id: {
                        "last_access": stats["last_access"],
                        "is_dirty": stats.get("is_dirty", False),
                        "age_seconds": time.time() - stats["last_access"],
                        "user_message_count": self._count_user_messages(self.context_cache[chat_id])
                    }
                    for chat_id, stats in self.cache_status.items()
                }
            }
            
        return status
        
    def _count_user_messages(self, context_data: Dict[str, Any]) -> int:
        """统计用户消息数量"""
        if not context_data or "data" not in context_data:
            return 0
            
        messages = context_data["data"].get("messages", [])
        count = 0
        for msg in messages:
            if msg.get("role") == "user":
                count += 1
        return count
        
    async def shutdown(self):
        """关闭异步上下文管理器"""
        self.is_running = False
        
        # 等待清理任务结束
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
                
        # 保存所有脏缓存
        async with self.lock:
            for chat_id, status in list(self.cache_status.items()):
                if status.get("is_dirty", False):
                    await self._save_context_to_file(chat_id)
                    
        self.logger.info("异步上下文管理器已关闭")