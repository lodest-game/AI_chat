#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
essentials_manager.py - 基础指令处理器
"""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple


class EssentialsManager:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.context_manager = None
        self.tool_manager = None
        self.config = None
        self.command_prefix = "#"
        self.admin_chats = set()
        
        self.commands = {
            "模型列表": self._handle_model_list,
            "模型查询": self._handle_model_query,
            "模型更换": self._handle_model_change,
            "工具支持": self._handle_tools_toggle,
            "提示词": self._handle_prompt_query,
            "设定提示词": self._handle_prompt_set,
            "删除提示词": self._handle_prompt_delete,
            "上下文清理": self._handle_context_clear,
            "删除上下文": self._handle_context_clear,
            "重载": self._handle_reload,
            "热重载": self._handle_reload,
            "帮助": self._handle_help
        }
        
    async def initialize(self, config: Dict[str, Any], **kwargs):
        self.config = config
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                
        essentials_config = config.get("system", {}).get("essentials_manager", {})
        self.admin_chats = set(essentials_config.get("admin_chats", []))
        
    def is_command(self, message_data: Dict[str, Any]) -> bool:
        if not message_data or "content" not in message_data or message_data.get("role") == "assistant":
            return False
            
        content = message_data["content"]
        
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if isinstance(text, str):
                        text_parts.append(text)
            
            if not text_parts:
                return False
                
            combined_text = " ".join(text_parts)
            return combined_text.strip().startswith(self.command_prefix)
            
        elif isinstance(content, str):
            return content.strip().startswith(self.command_prefix)
            
        return False
            
    async def execute_command(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            content = message_data.get("content", "")
            chat_id = message_data.get("chat_id", "")
            user_id = message_data.get("user_id", "")
            
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text", "")
                        if isinstance(text, str):
                            text_parts.append(text)
                content = " ".join(text_parts)
            elif not isinstance(content, str):
                content = str(content)
            
            command, args = await self._parse_command(content)
            if not command:
                return self._create_error_response("无效指令格式")
                
            if not await self._check_permission(chat_id, user_id, command):
                admin_commands = {"重载", "热重载"}
                if command in admin_commands:
                    return self._create_error_response("权限不足，此指令仅限管理员使用")
                else:
                    return self._create_error_response("权限不足")
                    
            handler = self.commands.get(command)
            if not handler:
                return self._create_error_response(f"未知指令: #{command}")
                
            result = await handler(args, chat_id, user_id)
            
            if "content" not in result:
                result["content"] = "指令执行成功"
                
            return result
            
        except Exception as e:
            self.logger.error(f"执行指令失败: {e}")
            return self._create_error_response(f"指令执行失败: {str(e)}")
            
    async def _parse_command(self, content: Any) -> Tuple[Optional[str], List[str]]:
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if isinstance(text, str):
                        text_parts.append(text)
            content = " ".join(text_parts) if text_parts else ""
        
        if not isinstance(content, str):
            return None, []
        
        if not content.startswith(self.command_prefix):
            return None, []
        
        content = content[len(self.command_prefix):].strip()
        parts = content.split()
        if not parts:
            return None, []
            
        command = parts[0]
        args = parts[1:] if len(parts) > 1 else []
        return command, args
        
    async def _check_permission(self, chat_id: str, user_id: str, command: str = None) -> bool:
        common_commands = {
            "模型列表", "模型查询", "模型更换", 
            "工具支持", "提示词", "设定提示词", "删除提示词",
            "上下文清理", "删除上下文", "帮助"
        }
        
        admin_commands = {"重载", "热重载"}
        
        if not command:
            return False
        
        if command in common_commands:
            return True
        
        elif command in admin_commands:
            return chat_id in self.admin_chats
        
        return False
        
    async def _handle_model_list(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        if not self.config:
            return self._create_error_response("配置未初始化")
            
        chat_mode = self.config.get("system", {}).get("context_manager", {}).get("chat_mode", {})
        model_list = []
        
        for mode, models in chat_mode.items():
            model_list.append(f"{mode}模式:")
            for model in models:
                model_list.append(f"  - {model}")
                
        response_text = "可用模型列表:\n" + "\n".join(model_list)
        
        return {
            "success": True,
            "content": response_text,
            "chat_id": chat_id,
            "command": "模型列表"
        }
        
    async def _handle_model_query(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        context_result = await self.context_manager.get_context(chat_id)
        if not context_result.get("success"):
            return self._create_error_response(context_result.get("error", "获取上下文失败"))
            
        context_data = context_result.get("data", {})
        current_model = context_data.get("data", {}).get("model", "未知模型")
        response_text = f"当前对话使用的模型: {current_model}"
        
        return {
            "success": True,
            "content": response_text,
            "chat_id": chat_id,
            "command": "模型查询",
            "current_model": current_model
        }
        
    async def _handle_model_change(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        if not args:
            return self._create_error_response("请指定要更换的模型名称")
            
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        new_model = args[0]
        chat_mode = self.config.get("system", {}).get("context_manager", {}).get("chat_mode", {})
        available_models = []
        for models in chat_mode.values():
            available_models.extend(models)
            
        if new_model not in available_models:
            return self._create_error_response(f"模型 '{new_model}' 不可用")
            
        update_result = await self.context_manager.update_model(chat_id, new_model)
        if not update_result.get("success"):
            return self._create_error_response(update_result.get("error", "更换模型失败"))
            
        return {
            "success": True,
            "content": f"模型已更换为: {new_model}",
            "chat_id": chat_id,
            "command": "模型更换",
            "new_model": new_model
        }
        
    async def _handle_tools_toggle(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        if not args:
            return self._create_error_response("请指定 true 或 false")
            
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        value_str = args[0].lower()
        if value_str not in ["true", "false"]:
            return self._create_error_response("参数必须是 true 或 false")
            
        enable_tools = value_str == "true"
        update_result = await self.context_manager.update_tools_call(chat_id, enable_tools)
        if not update_result.get("success"):
            return self._create_error_response(update_result.get("error", "设置工具支持失败"))
            
        status_text = "启用" if enable_tools else "禁用"
        
        return {
            "success": True,
            "content": f"工具支持已{status_text}",
            "chat_id": chat_id,
            "command": "工具支持",
            "tools_call_enabled": enable_tools
        }
        
    async def _handle_prompt_query(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        get_result = await self.context_manager.get_custom_prompt(chat_id)
        if not get_result.get("success"):
            return self._create_error_response(get_result.get("error", "获取提示词失败"))
            
        has_custom = get_result.get("has_custom_prompt", False)
        
        if has_custom:
            custom_prompt = get_result.get("custom_prompt", "")
            response_text = f"当前对话的专属提示词:\n{custom_prompt}"
        else:
            response_text = "当前对话没有设置专属提示词，使用默认核心提示词"
            
        return {
            "success": True,
            "content": response_text,
            "chat_id": chat_id,
            "command": "提示词",
            "has_custom_prompt": has_custom,
            "custom_prompt": get_result.get("custom_prompt", "")
        }
        
    async def _handle_prompt_set(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        if not args:
            return self._create_error_response("请指定要设置的提示词内容")
            
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        new_prompt = " ".join(args)
        update_result = await self.context_manager.update_custom_prompt(chat_id, new_prompt)
        if not update_result.get("success"):
            return self._create_error_response(update_result.get("error", "设置提示词失败"))
            
        return {
            "success": True,
            "content": f"专属提示词已设置:\n{new_prompt}",
            "chat_id": chat_id,
            "command": "设定提示词",
            "new_prompt": new_prompt
        }
        
    async def _handle_prompt_delete(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        delete_result = await self.context_manager.delete_custom_prompt(chat_id)
        if not delete_result.get("success"):
            return self._create_error_response(delete_result.get("error", "删除提示词失败"))
            
        return {
            "success": True,
            "content": "专属提示词已删除",
            "chat_id": chat_id,
            "command": "删除提示词"
        }
        
    async def _handle_context_clear(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        clear_result = await self.context_manager.clear_context(chat_id)
        if not clear_result.get("success"):
            return self._create_error_response(clear_result.get("error", "清理上下文失败"))
            
        return {
            "success": True,
            "content": "对话上下文已清理",
            "chat_id": chat_id,
            "command": "上下文清理"
        }
        
    async def _handle_reload(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        if not self.tool_manager:
            return self._create_error_response("工具管理器未初始化")
            
        reload_result = await self.tool_manager.reload_tools()
        if not reload_result.get("success"):
            return self._create_error_response(reload_result.get("error", "重载工具失败"))
            
        return {
            "success": True,
            "content": "工具系统已重载",
            "chat_id": chat_id,
            "command": "重载"
        }
        
    async def _handle_help(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        try:
            all_commands = [
                ("#模型列表", "查看所有可用模型", "普通指令"),
                ("#模型查询", "查看当前对话使用的模型", "普通指令"),
                ("#模型更换 <模型名>", "更换当前对话的模型", "普通指令"),
                ("#工具支持 <true/false>", "启用/禁用工具调用", "普通指令"),
                ("#提示词", "查看当前对话的专属提示词", "普通指令"),
                ("#设定提示词 <内容>", "设置专属提示词", "普通指令"),
                ("#删除提示词", "删除专属提示词", "普通指令"),
                ("#上下文清理 / #删除上下文", "清理当前对话的上下文", "普通指令"),
                ("#重载 / #热重载", "重新加载工具系统", "管理员指令"),
                ("#帮助", "显示此帮助信息", "普通指令")
            ]
            
            help_text = "📚 可用指令列表:\n\n"
            
            for cmd, desc, perm in all_commands:
                if perm == "管理员指令":
                    help_text += f"🔒 {cmd}\n   {desc} ({perm})\n\n"
                else:
                    help_text += f"📝 {cmd}\n   {desc}\n\n"
            
            help_text += "📌 说明:\n"
            help_text += "- 普通指令：所有用户均可使用\n"
            help_text += "- 管理员指令：仅限配置的管理员私聊使用\n"
            
            return {
                "success": True,
                "content": help_text,
                "chat_id": chat_id,
                "command": "帮助"
            }
            
        except Exception as e:
            self.logger.error(f"生成帮助信息失败: {e}")
            return self._create_error_response(f"生成帮助信息失败: {str(e)}")
        
    def _create_error_response(self, error_msg: str) -> Dict[str, Any]:
        return {
            "success": False,
            "content": f"错误: {error_msg}",
            "error": error_msg
        }
        
    def get_supported_commands(self) -> List[str]:
        return list(self.commands.keys())
        
    def add_admin_chat(self, chat_id: str):
        self.admin_chats.add(chat_id)
        
    def remove_admin_chat(self, chat_id: str):
        if chat_id in self.admin_chats:
            self.admin_chats.remove(chat_id)