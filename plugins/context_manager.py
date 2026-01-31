#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
context_manager.py - 异步上下文管理器
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
    def __init__(self, history_dir: Path):
        self.logger = logging.getLogger(__name__)
        self.history_dir = Path(history_dir)
        self.context_cache = {}
        self.cache_status = {}
        self.config = None
        self.lock = asyncio.Lock()
        self.cleanup_task = None
        self.is_running = False
        self.tool_manager = None
        
        self.default_config = {
            "default_model": "local_model",
            "chat_mode": {"LLM": ["local_model"], "MLLM": []},
            "default_tools_call": True,
            "model": {
                "max_tokens": 64000,
                "temperature": 0.1,
                "stream": False
            },
            "core_prompt": ["你是群聊成员"],
            "max_user_messages_per_chat": 20,
            "cache_inactive_unload_seconds": 1800
        }
        
    async def initialize(self, config: Dict[str, Any]):
        self.config = config.get("system", {}).get("context_manager", self.default_config)
        self.history_dir.mkdir(exist_ok=True)
        
        self.max_user_messages_per_chat = self.config.get("max_user_messages_per_chat", 20)
        self.cache_inactive_unload_seconds = self.config.get("cache_inactive_unload_seconds", 1800)
        
        self.core_prompt = self.config.get("core_prompt", ["你是群聊成员"])
        if isinstance(self.core_prompt, list):
            self.core_prompt = "\n".join(self.core_prompt)
            
        self.is_running = True
        self.cleanup_task = asyncio.create_task(self._cleanup_daemon())
        
    async def _cleanup_daemon(self):
        while self.is_running:
            await asyncio.sleep(60)
            current_time = time.time()
            to_unload = []
            
            async with self.lock:
                for chat_id, status in list(self.cache_status.items()):
                    inactive_time = current_time - status["last_access"]
                    if inactive_time >= self.cache_inactive_unload_seconds:
                        to_unload.append(chat_id)
            
            for chat_id in to_unload:
                await self._save_context_if_dirty(chat_id)
                await self._remove_from_cache(chat_id)
                
    def _get_context_file_path(self, chat_id: str) -> Path:
        safe_chat_id = re.sub(r'[<>:"/\\|?*]', '_', str(chat_id))
        
        if len(safe_chat_id) > 200:
            hash_part = hashlib.md5(chat_id.encode()).hexdigest()[:8]
            safe_chat_id = f"{safe_chat_id[:150]}_{hash_part}"
        
        return self.history_dir / f"{safe_chat_id}.json"
        
    async def _load_context_from_file(self, chat_id: str) -> Optional[Dict[str, Any]]:
        file_path = self._get_context_file_path(chat_id)
        
        if not file_path.exists():
            return None
            
        try:
            loop = asyncio.get_event_loop()
            with open(file_path, 'r', encoding='utf-8') as f:
                return await loop.run_in_executor(None, json.load, f)
        except Exception as e:
            self.logger.error(f"异步加载上下文文件失败 {chat_id}: {e}")
            return None
            
    async def _save_context_to_file(self, chat_id: str):
        if chat_id not in self.context_cache:
            return
            
        context_data = self.context_cache.get(chat_id)
        if not context_data:
            return
            
        file_path = self._get_context_file_path(chat_id)
        
        try:
            loop = asyncio.get_event_loop()
            file_path.parent.mkdir(exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                await loop.run_in_executor(
                    None, 
                    lambda: json.dump(context_data, f, ensure_ascii=False, indent=2)
                )
                
            async with self.lock:
                if chat_id in self.cache_status:
                    self.cache_status[chat_id]["is_dirty"] = False
        except Exception as e:
            self.logger.error(f"异步保存上下文文件失败 {chat_id}: {e}")
            
    async def _save_context_if_dirty(self, chat_id: str):
        if chat_id in self.cache_status and self.cache_status[chat_id].get("is_dirty", False):
            await self._save_context_to_file(chat_id)
            
    async def _remove_from_cache(self, chat_id: str):
        async with self.lock:
            self.context_cache.pop(chat_id, None)
            self.cache_status.pop(chat_id, None)
                
    def _ensure_default_context(self, chat_id: str) -> Dict[str, Any]:
        chat_mode = self._determine_chat_mode(chat_id)
        
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
                "temperature": self.config.get("model", {}).get("temperature", 0.1),
                "stream": self.config.get("model", {}).get("stream", False)
            }
        }
        
        if self.tool_manager:
            context_data["data"]["tools"] = self.tool_manager.get_tool_definitions()
        
        return context_data
        
    def _determine_chat_mode(self, chat_id: str) -> str:
        chat_mode_config = self.config.get("chat_mode", {})
        
        if chat_mode_config.get("LLM"):
            return "LLM"
        elif chat_mode_config.get("MLLM"):
            return "MLLM"
        else:
            return "LLM"
            
    def set_tool_manager(self, tool_manager):
        self.tool_manager = tool_manager
    
    async def _sync_tools_to_context(self, chat_id: str, context_data: Dict[str, Any]):
        if not self.tool_manager:
            return
            
        try:
            current_tools = self.tool_manager.get_tool_definitions()
            if "data" in context_data:
                context_data["data"]["tools"] = current_tools
        except Exception as e:
            self.logger.error(f"异步同步工具定义失败 {chat_id}: {e}")
    
    async def get_context(self, chat_id: str) -> Dict[str, Any]:
        try:
            async with self.lock:
                if chat_id in self.context_cache:
                    if chat_id in self.cache_status:
                        self.cache_status[chat_id]["last_access"] = time.time()
                    return {"success": True, "data": self.context_cache[chat_id], "from_cache": True}
                    
                context_data = await self._load_context_from_file(chat_id)
                
                if context_data:
                    await self._sync_tools_to_context(chat_id, context_data)
                    self.context_cache[chat_id] = context_data
                    self.cache_status[chat_id] = {"last_access": time.time(), "is_dirty": False}
                    return {"success": True, "data": context_data, "from_cache": False, "from_file": True}
                    
                context_data = self._ensure_default_context(chat_id)
                self.context_cache[chat_id] = context_data
                self.cache_status[chat_id] = {"last_access": time.time(), "is_dirty": True}
                return {"success": True, "data": context_data, "from_cache": False, "from_file": False, "is_new": True}
                
        except Exception as e:
            self.logger.error(f"异步获取上下文失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def _trim_context_messages(self, context_data: Dict[str, Any]):
        if "data" not in context_data or "messages" not in context_data["data"]:
            return
            
        messages = context_data["data"]["messages"]
        user_message_count = sum(1 for msg in messages if msg.get("role") == "user")
        
        if user_message_count <= self.max_user_messages_per_chat:
            return
            
        messages_to_remove = user_message_count - self.max_user_messages_per_chat
        removed_count = 0
        i = 0
        
        while i < len(messages) and removed_count < messages_to_remove:
            role = messages[i].get("role")
            
            if role == "system":
                i += 1
                continue
                
            if role == "user":
                start_index = i
                end_index = i + 1
                while end_index < len(messages) and messages[end_index].get("role") == "assistant":
                    end_index += 1
                
                deleted_messages = messages[start_index:end_index]
                del messages[start_index:end_index]
                removed_count += sum(1 for msg in deleted_messages if msg.get("role") == "user")
            else:
                del messages[i]
        
        final_user_count = sum(1 for msg in messages if msg.get("role") == "user")
        self.logger.debug(f"修剪完成: 删除了 {removed_count} 个用户消息，剩余 {final_user_count} 个用户消息")
            
    async def update_context(self, chat_id: str, message_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            context_result = await self.get_context(chat_id)
            if not context_result.get("success"):
                return context_result
                
            context_data = context_result["data"]
            role = message_data.get("role", "user")
            is_ai_message = role == "assistant"
            
            if is_ai_message:
                ai_message = message_data.get("message", {})
                if ai_message.get("role") == "assistant":
                    context_data["data"]["messages"].append(ai_message)
            else:
                message_content = await self._extract_message_content(message_data)
                if not message_content:
                    return {"success": False, "error": "无法提取消息内容"}
                    
                user_message = {"role": "user", "content": message_content}
                context_data["data"]["messages"].append(user_message)
            
            await self._trim_context_messages(context_data)
            
            async with self.lock:
                if chat_id in self.cache_status:
                    self.cache_status[chat_id]["is_dirty"] = True
                    self.cache_status[chat_id]["last_access"] = time.time()
                    
            return {"success": True, "data": context_data}
            
        except Exception as e:
            self.logger.error(f"异步更新上下文失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def _extract_message_content(self, message_data: Dict[str, Any]) -> Any:
        if not message_data or "content" not in message_data:
            return None
            
        content = message_data["content"]
        
        if isinstance(content, str):
            return content
            
        if isinstance(content, list):
            if not content:
                return "[图片消息]"
                
            text_parts = []
            has_text = False
            image_count = 0
            images_info = []
            
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
                        images_info.append(item)
                        
            if has_text:
                return content
            elif image_count > 0:
                result = []
                result.append({"type": "text", "text": "[图片消息]" if image_count == 1 else f"[{image_count}张图片]"})
                result.extend(images_info)
                return result
            else:
                return "[消息]"
                
        return str(content)
        
    async def update_model(self, chat_id: str, model_name: str) -> Dict[str, Any]:
        try:
            await self._save_context_if_dirty(chat_id)
            await self._remove_from_cache(chat_id)
            
            context_data = await self._load_context_from_file(chat_id)
            if not context_data:
                context_data = self._ensure_default_context(chat_id)
            
            context_data["data"]["model"] = model_name
            self.context_cache[chat_id] = context_data
            self.cache_status[chat_id] = {"last_access": time.time(), "is_dirty": True}
            
            await self._save_context_to_file(chat_id)
            
            return {"success": True, "message": f"模型已更新为: {model_name}"}
            
        except Exception as e:
            self.logger.error(f"异步更新模型失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def update_tools_call(self, chat_id: str, enable: bool) -> Dict[str, Any]:
        try:
            await self._save_context_if_dirty(chat_id)
            await self._remove_from_cache(chat_id)
            
            context_data = await self._load_context_from_file(chat_id)
            if not context_data:
                context_data = self._ensure_default_context(chat_id)
            
            context_data["tools_call"] = enable
            self.context_cache[chat_id] = context_data
            self.cache_status[chat_id] = {"last_access": time.time(), "is_dirty": True}
            
            await self._save_context_to_file(chat_id)
            
            status_text = "启用" if enable else "禁用"
            return {"success": True, "message": f"工具调用已{status_text}"}
            
        except Exception as e:
            self.logger.error(f"异步更新工具调用失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def update_custom_prompt(self, chat_id: str, prompt_content: str) -> Dict[str, Any]:
        try:
            await self._save_context_if_dirty(chat_id)
            await self._remove_from_cache(chat_id)
            
            context_data = await self._load_context_from_file(chat_id)
            if not context_data:
                context_data = self._ensure_default_context(chat_id)
            
            messages = context_data["data"].get("messages", [])
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
                
            if prompt_content and prompt_content.strip():
                full_prompt = f"{prompt_content.strip()}\n{self.core_prompt}"
            else:
                full_prompt = self.core_prompt
                
            system_message["content"] = full_prompt
            messages[system_message_index] = system_message
            context_data["data"]["messages"] = messages
            
            self.context_cache[chat_id] = context_data
            self.cache_status[chat_id] = {"last_access": time.time(), "is_dirty": True}
            
            await self._save_context_to_file(chat_id)
            
            action_text = "更新" if prompt_content and prompt_content.strip() else "删除"
            return {"success": True, "message": f"专属提示词已{action_text}"}
            
        except Exception as e:
            self.logger.error(f"异步更新专属提示词失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def delete_custom_prompt(self, chat_id: str) -> Dict[str, Any]:
        try:
            await self._save_context_if_dirty(chat_id)
            await self._remove_from_cache(chat_id)
            
            context_data = await self._load_context_from_file(chat_id)
            if not context_data:
                context_data = self._ensure_default_context(chat_id)
            
            messages = context_data["data"].get("messages", [])
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
                
            system_message["content"] = self.core_prompt
            messages[system_message_index] = system_message
            context_data["data"]["messages"] = messages
            
            self.context_cache[chat_id] = context_data
            self.cache_status[chat_id] = {"last_access": time.time(), "is_dirty": True}
            
            await self._save_context_to_file(chat_id)
            
            return {"success": True, "message": "专属提示词已删除"}
            
        except Exception as e:
            self.logger.error(f"异步删除专属提示词失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_custom_prompt(self, chat_id: str) -> Dict[str, Any]:
        try:
            context_result = await self.get_context(chat_id)
            if not context_result.get("success"):
                return {"success": False, "error": context_result.get("error", "获取上下文失败")}
                
            context_data = context_result["data"]
            messages = context_data.get("data", {}).get("messages", [])
            
            for message in messages:
                if message.get("role") == "system":
                    content = message.get("content", "")
                    if content == self.core_prompt:
                        return {"success": True, "chat_id": chat_id, "has_custom_prompt": False, "custom_prompt": "", "core_prompt": self.core_prompt}
                    elif self.core_prompt in content:
                        custom_prompt = content.replace(self.core_prompt, "").strip()
                        return {"success": True, "chat_id": chat_id, "has_custom_prompt": bool(custom_prompt), "custom_prompt": custom_prompt, "core_prompt": self.core_prompt}
                    break
            
            return {"success": True, "chat_id": chat_id, "has_custom_prompt": False, "custom_prompt": "", "core_prompt": self.core_prompt}
            
        except Exception as e:
            self.logger.error(f"异步获取专属提示词失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def update_tools_definition(self, chat_id: str, tools_definition: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            await self._save_context_if_dirty(chat_id)
            await self._remove_from_cache(chat_id)
            
            context_data = await self._load_context_from_file(chat_id)
            if not context_data:
                context_data = self._ensure_default_context(chat_id)
            
            context_data["data"]["tools"] = tools_definition
            self.context_cache[chat_id] = context_data
            self.cache_status[chat_id] = {"last_access": time.time(), "is_dirty": True}
            
            await self._save_context_to_file(chat_id)
            
            return {"success": True, "message": f"工具定义已更新 ({len(tools_definition)} 个工具)"}
            
        except Exception as e:
            self.logger.error(f"异步更新工具定义失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def clear_context(self, chat_id: str) -> Dict[str, Any]:
        try:
            file_path = self._get_context_file_path(chat_id)
            if file_path.exists():
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, file_path.unlink)
                
            async with self.lock:
                self.context_cache.pop(chat_id, None)
                self.cache_status.pop(chat_id, None)
                    
            return {"success": True, "message": "对话上下文已清理"}
            
        except Exception as e:
            self.logger.error(f"异步清理上下文失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def get_cache_status(self) -> Dict[str, Any]:
        async with self.lock:
            return {
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
        
    def _count_user_messages(self, context_data: Dict[str, Any]) -> int:
        if not context_data or "data" not in context_data:
            return 0
            
        messages = context_data["data"].get("messages", [])
        return sum(1 for msg in messages if msg.get("role") == "user")
        
    async def shutdown(self):
        self.is_running = False
        
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
                
        async with self.lock:
            for chat_id, status in list(self.cache_status.items()):
                if status.get("is_dirty", False):
                    await self._save_context_to_file(chat_id)