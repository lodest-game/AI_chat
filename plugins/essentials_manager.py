#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
essentials_manager.py - 基础指令处理器
处理系统基础指令，提供核心管理功能
完全异步版本
"""

import re
import logging
import json
from typing import Dict, Any, List, Optional, Tuple


class EssentialsManager:
    """基础指令处理器"""
    
    def __init__(self):
        """初始化指令处理器"""
        self.logger = logging.getLogger(__name__)
        
        # 模块引用
        self.context_manager = None
        self.tool_manager = None
        
        # 配置
        self.config = None
        
        # 指令前缀
        self.command_prefix = "#"
        
        # 权限控制
        self.admin_chats = set()  # 管理员私聊ID集合
        self.permission_required = True  # 是否需要权限验证
        
        # 支持的指令列表
        self.commands = {
            "模型列表": self._handle_model_list,
            "模型查询": self._handle_model_query,
            "模型更换": self._handle_model_change,
            "工具支持": self._handle_tools_toggle,
            "提示词": self._handle_prompt_query,
            "设定提示词": self._handle_prompt_set,
            "删除提示词": self._handle_prompt_delete,
            "上下文清理": self._handle_context_clear,
            "删除上下文": self._handle_context_clear,  # 别名
            "重载": self._handle_reload,
            "热重载": self._handle_reload,  # 别名
            "帮助": self._handle_help  # 新增帮助指令
        }
        
    async def initialize(self, config: Dict[str, Any], **kwargs):
        """初始化指令处理器"""
        self.config = config
        
        # 设置模块引用
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                
        # 从配置中读取权限设置
        essentials_config = config.get("system", {}).get("essentials_manager", {})
        self.permission_required = essentials_config.get("permission_required", True)
        
        # 读取管理员chat_id列表
        self.admin_chats = set(essentials_config.get("admin_chats", []))
        
        self.logger.info(f"基础指令处理器初始化完成，管理员chat数: {len(self.admin_chats)}")
        
    def is_command(self, message_data: Dict[str, Any]) -> bool:
        """判断消息是否是指令"""
        if not message_data or "content" not in message_data:
            return False
            
        # 如果是AI回复，不处理指令
        if message_data.get("role") == "assistant":
            return False
            
        # 提取消息内容
        content = message_data["content"]
        
        # 处理不同类型的content
        if isinstance(content, list):
            # 多模态消息，查找文本部分
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if isinstance(text, str):
                        text_parts.append(text)
            
            if not text_parts:
                return False
                
            # 合并所有文本部分
            combined_text = " ".join(text_parts)
            return combined_text.strip().startswith(self.command_prefix)
            
        elif isinstance(content, str):
            # 字符串消息
            return content.strip().startswith(self.command_prefix)
            
        else:
            return False
            
    async def execute_command(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行指令（异步版本）
        
        Args:
            message_data: 消息数据
            
        Returns:
            指令执行结果
        """
        try:
            # 提取指令内容
            content = message_data.get("content", "")
            chat_id = message_data.get("chat_id", "")
            user_id = message_data.get("user_id", "")
            
            # 处理多模态消息
            if isinstance(content, list):
                # 提取文本部分
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text", "")
                        if isinstance(text, str):
                            text_parts.append(text)
                content = " ".join(text_parts)
            elif not isinstance(content, str):
                content = str(content)
            
            # 解析指令
            command, args = await self._parse_command(content)
            if not command:
                return self._create_error_response("无效指令格式")
                
            # 权限验证 - 传入指令名称
            if not await self._check_permission(chat_id, user_id, command):
                # 根据指令类型返回不同的错误信息
                admin_commands = {"重载", "热重载"}
                if command in admin_commands:
                    return self._create_error_response("权限不足，此指令仅限管理员使用")
                else:
                    return self._create_error_response("权限不足")
                    
            # 查找指令处理器
            handler = self.commands.get(command)
            if not handler:
                return self._create_error_response(f"未知指令: #{command}")
                
            # 执行指令（异步调用）
            result = await handler(args, chat_id, user_id)
            
            # 确保返回格式
            if "content" not in result:
                result["content"] = "指令执行成功"
                
            return result
            
        except Exception as e:
            self.logger.error(f"执行指令失败: {e}")
            return self._create_error_response(f"指令执行失败: {str(e)}")
            
    async def _parse_command(self, content: Any) -> Tuple[Optional[str], List[str]]:
        """解析指令（异步版本）"""
        # 如果content是列表，提取文本部分
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if isinstance(text, str):
                        text_parts.append(text)
            content = " ".join(text_parts) if text_parts else ""
        
        # 确保content是字符串
        if not isinstance(content, str):
            return None, []
        
        # 检查是否以指令前缀开头
        if not content.startswith(self.command_prefix):
            return None, []
        
        # 移除前缀和首尾空格
        content = content[len(self.command_prefix):].strip()
        
        # 分割指令和参数
        parts = content.split()
        if not parts:
            return None, []
            
        command = parts[0]
        args = parts[1:] if len(parts) > 1 else []
        
        return command, args
        
    async def _check_permission(self, chat_id: str, user_id: str, command: str = None) -> bool:
        """检查权限（异步版本）"""
        # 所有用户都可以执行的普通指令
        common_commands = {
            "模型列表", "模型查询", "模型更换", 
            "工具支持", "提示词", "设定提示词", "删除提示词",
            "上下文清理", "删除上下文", "帮助"
        }
        
        # 需要管理员权限的指令
        admin_commands = {"重载", "热重载"}
        
        # 如果没有指定指令，默认拒绝（安全第一）
        if not command:
            self.logger.warning(f"未指定指令的权限检查: chat_id={chat_id}")
            return False
        
        # 检查指令类型
        if command in common_commands:
            # 普通指令，所有用户都可以执行
            self.logger.debug(f"普通指令 '{command}' 允许执行: chat_id={chat_id}")
            return True
        
        elif command in admin_commands:
            # 管理员指令，需要检查是否在管理员列表
            if chat_id in self.admin_chats:
                self.logger.debug(f"管理员指令 '{command}' 允许执行: chat_id={chat_id}")
                return True
            else:
                self.logger.warning(f"非管理员尝试执行管理员指令: chat_id={chat_id}, command={command}")
                return False
        
        # 未知指令默认拒绝
        self.logger.warning(f"未知指令权限检查: command={command}, chat_id={chat_id}")
        return False
        
    async def _handle_model_list(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        """处理#模型列表指令（异步版本）"""
        if not self.config:
            return self._create_error_response("配置未初始化")
            
        # 获取模型列表
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
        """处理#模型查询指令（异步版本）"""
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        # 获取当前对话的模型
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
        """处理#模型更换指令（异步版本）"""
        if not args:
            return self._create_error_response("请指定要更换的模型名称")
            
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        new_model = args[0]
        
        # 验证模型是否可用
        chat_mode = self.config.get("system", {}).get("context_manager", {}).get("chat_mode", {})
        available_models = []
        for models in chat_mode.values():
            available_models.extend(models)
            
        if new_model not in available_models:
            return self._create_error_response(f"模型 '{new_model}' 不可用")
            
        # 更新上下文中的模型
        update_result = await self.context_manager.update_model(chat_id, new_model)
        if not update_result.get("success"):
            return self._create_error_response(update_result.get("error", "更换模型失败"))
            
        response_text = f"模型已更换为: {new_model}"
        
        return {
            "success": True,
            "content": response_text,
            "chat_id": chat_id,
            "command": "模型更换",
            "new_model": new_model
        }
        
    async def _handle_tools_toggle(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        """处理#工具支持指令（异步版本）"""
        if not args:
            return self._create_error_response("请指定 true 或 false")
            
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        value_str = args[0].lower()
        if value_str not in ["true", "false"]:
            return self._create_error_response("参数必须是 true 或 false")
            
        enable_tools = value_str == "true"
        
        # 更新工具调用开关
        update_result = await self.context_manager.update_tools_call(chat_id, enable_tools)
        if not update_result.get("success"):
            return self._create_error_response(update_result.get("error", "设置工具支持失败"))
            
        status_text = "启用" if enable_tools else "禁用"
        response_text = f"工具支持已{status_text}"
        
        return {
            "success": True,
            "content": response_text,
            "chat_id": chat_id,
            "command": "工具支持",
            "tools_call_enabled": enable_tools
        }
        
    async def _handle_prompt_query(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        """处理#提示词指令（异步版本）"""
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        # 获取专属提示词
        get_result = await self.context_manager.get_custom_prompt(chat_id)
        if not get_result.get("success"):
            error_msg = get_result.get("error", "获取提示词失败")
            self.logger.error(f"获取提示词失败: {error_msg}")
            return self._create_error_response(error_msg)
            
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
        """处理#设定提示词指令（异步版本）"""
        if not args:
            return self._create_error_response("请指定要设置的提示词内容")
            
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        new_prompt = " ".join(args)
        
        # 更新专属提示词
        update_result = await self.context_manager.update_custom_prompt(chat_id, new_prompt)
        if not update_result.get("success"):
            error_msg = update_result.get("error", "设置提示词失败")
            self.logger.error(f"设置提示词失败: {error_msg}")
            return self._create_error_response(error_msg)
            
        response_text = f"专属提示词已设置:\n{new_prompt}"
        
        return {
            "success": True,
            "content": response_text,
            "chat_id": chat_id,
            "command": "设定提示词",
            "new_prompt": new_prompt
        }
        
    async def _handle_prompt_delete(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        """处理#删除提示词指令（异步版本）"""
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        # 删除专属提示词
        delete_result = await self.context_manager.delete_custom_prompt(chat_id)
        if not delete_result.get("success"):
            error_msg = delete_result.get("error", "删除提示词失败")
            self.logger.error(f"删除提示词失败: {error_msg}")
            return self._create_error_response(error_msg)
            
        response_text = "专属提示词已删除"
        
        return {
            "success": True,
            "content": response_text,
            "chat_id": chat_id,
            "command": "删除提示词"
        }
        
    async def _handle_context_clear(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        """处理#上下文清理指令（异步版本）"""
        if not self.context_manager:
            return self._create_error_response("上下文管理器未初始化")
            
        # 清理上下文
        clear_result = await self.context_manager.clear_context(chat_id)
        if not clear_result.get("success"):
            return self._create_error_response(clear_result.get("error", "清理上下文失败"))
            
        response_text = "对话上下文已清理"
        
        return {
            "success": True,
            "content": response_text,
            "chat_id": chat_id,
            "command": "上下文清理"
        }
        
    async def _handle_reload(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        """处理#重载指令（异步版本）"""
        if not self.tool_manager:
            return self._create_error_response("工具管理器未初始化")
            
        # 触发工具重载
        reload_result = await self.tool_manager.reload_tools()
        if not reload_result.get("success"):
            return self._create_error_response(reload_result.get("error", "重载工具失败"))
            
        response_text = "工具系统已重载"
        
        return {
            "success": True,
            "content": response_text,
            "chat_id": chat_id,
            "command": "重载"
        }
        
    async def _handle_help(self, args: List[str], chat_id: str, user_id: str) -> Dict[str, Any]:
        """处理#帮助指令（异步版本）"""
        try:
            # 所有指令列表
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
            
            # 构建帮助文本
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
        """创建错误响应"""
        return {
            "success": False,
            "content": f"错误: {error_msg}",
            "error": error_msg
        }
        
    def get_supported_commands(self) -> List[str]:
        """获取支持的指令列表"""
        return list(self.commands.keys())
        
    def add_admin_chat(self, chat_id: str):
        """添加管理员私聊"""
        self.admin_chats.add(chat_id)
        self.logger.info(f"已添加管理员私聊: {chat_id}")
        
    def remove_admin_chat(self, chat_id: str):
        """移除管理员私聊"""
        if chat_id in self.admin_chats:
            self.admin_chats.remove(chat_id)
            self.logger.info(f"已移除管理员私聊: {chat_id}")
        
    async def shutdown(self):
        """关闭指令处理器"""
        self.logger.info("基础指令处理器已关闭")