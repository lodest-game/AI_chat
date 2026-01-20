#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config_manager.py - 系统配置管理器
"""

import json
import logging
import copy
from pathlib import Path
from typing import Dict, Any


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, plugins_dir: Path):
        self.logger = logging.getLogger(__name__)
        self.plugins_dir = Path(plugins_dir)
        self.config_file = self.plugins_dir / "system.json"
        self.config = {}
        self.default_config = self._create_default_config()
        self.config_change_callbacks = {}
        
    def _create_default_config(self) -> Dict[str, Any]:
        return {
            "system": {
                "context_manager": {
                    "default_model": "local_model",
                    "chat_mode": {"LLM": [], "MLLM": ["local_model"]},
                    "default_tools_call": True,
                    "model": {"max_tokens": 64000, "temperature": 0.1, "stream": False},
                    "core_prompt": [
                        "你是一个即时聊天的参与者。在群聊中存在多个用户会同时发言。",
                        "【自主决策指南】"
                        "作为自主即时聊天参与者，请遵循以下决策原则："
                        "1. 工具调用判断：只有在用户明确要求工具相关操作时调用工具"
                        "2. 总结类请求：用户要求总结时，专注分析对话历史即可，无需外部工具"
                        "3. 决策信心度：如果不确定是否需要工具，优先选择不调用"
                        "4. 意图验证：如果用户意图模糊，可通过对话澄清，而非直接调用工具"
                        "你所处的环境是即时聊天平台，请关注当前问题，历史问题作为聊天背景，面对即时聊天，存在大量无效噪音，请过滤无效信息后回答。",
                        "接受的信息格式中，“发言人”表示发言人昵称。“发言内容”表示具体的用户讨论信息。",
                        "消息格式中“发言人”表示发言者身份，例如“发言人：腾讯网”表示“腾讯网“这个用户说的话。",
                        "请以自然、流畅的方式参与对话，直接回应当前用户的问题或评论。",
                        "不需要复述完整的用户发言内容，只需针对性地回复。",
                        "对于群聊中的多人讨论，可以自然地引用或回应特定用户。",
                        "对于相关工具定义不存在的功能请求，请告知用户无法做到，而不是使用虚拟的回应。",
                        "保持对话连贯，避免机械化地重复格式信息。"
                    ],
                    "max_user_messages_per_chat": 20,
                    "virtual_reply_enabled": True,
                    "virtual_reply_text": "已跳过此信息",
                    "cache_inactive_unload_seconds": 1800,
                },
                "rules_manager": {"mode": "wait"},
                "port_manager": {"reconnect_interval": 10, "max_reconnect_attempts": 3},
                "essentials_manager": {
                    "enable_model_management": True,
                    "enable_prompt_management": True,
                    "enable_tool_management": True,
                    "permission_required": True,
                    "admin_chats": ["qq_private_1308213863"]
                },
                "session_manager": {"session_timeout_minutes": 10, "max_sessions": 100},
                "tool_manager": {"auto_discovery": True, "generate_stubs": True}
            }
        }
        
    async def initialize(self) -> Dict[str, Any]:
        self.logger.info("初始化配置管理器...")
        
        self.plugins_dir.mkdir(exist_ok=True)
        
        if self.config_file.exists():
            self.config = await self._load_config()
        else:
            self.config = self.default_config
            await self._save_config()
            
        if not self._validate_config():
            self.logger.warning("配置验证失败，使用默认配置")
            self.config = self.default_config
            await self._save_config()
            
        return self.config
        
    async def _load_config(self) -> Dict[str, Any]:
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            self.logger.error(f"配置文件JSON格式错误: {e}")
            raise
            
    async def _save_config(self) -> bool:
        try:
            self.config_file.parent.mkdir(exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
            return False
            
    def _validate_config(self) -> bool:
        if not self.config or "system" not in self.config:
            return False
            
        system_config = self.config["system"]
        
        for module in ["context_manager", "rules_manager", "port_manager", "essentials_manager", "session_manager", "tool_manager"]:
            if module not in system_config:
                system_config[module] = self.default_config["system"].get(module, {})
                
        # 验证context_manager必要字段
        context_config = system_config.get("context_manager", {})
        for field in ["default_model", "chat_mode", "default_tools_call", "model", "core_prompt", "max_user_messages_per_chat", "cache_inactive_unload_seconds"]:
            if field not in context_config:
                context_config[field] = self.default_config["system"]["context_manager"].get(field)
                
        return True
        
    def get_config(self, module: str = None, key: str = None) -> Any:
        try:
            if module is None and key is None:
                return copy.deepcopy(self.config)
                
            if module is not None and key is None:
                if "system" in self.config and module in self.config["system"]:
                    return copy.deepcopy(self.config["system"][module])
                    
            if module is not None and key is not None:
                if ("system" in self.config and module in self.config["system"] and key in self.config["system"][module]):
                    return copy.deepcopy(self.config["system"][module][key])
                    
        except Exception as e:
            self.logger.error(f"获取配置失败: {e}")
            
        return None
        
    async def update_config(self, module: str, key: str, value: Any) -> bool:
        try:
            if "system" not in self.config:
                self.config["system"] = {}
                
            if module not in self.config["system"]:
                self.config["system"][module] = {}
                
            self.config["system"][module][key] = value
            success = await self._save_config()
            
            if success and module in self.config_change_callbacks:
                for callback in self.config_change_callbacks[module]:
                    if callable(callback):
                        await callback(module, key, value)
                        
            return success
            
        except Exception as e:
            self.logger.error(f"更新配置失败: {e}")
            return False
            
    def register_config_change_callback(self, module: str, callback):
        if module not in self.config_change_callbacks:
            self.config_change_callbacks[module] = []
        self.config_change_callbacks[module].append(callback)
        
    def get_default_config(self) -> Dict[str, Any]:
        return copy.deepcopy(self.default_config)
        
    async def reset_to_defaults(self) -> bool:
        try:
            self.config = self.default_config
            success = await self._save_config()
            
            if success:
                for module in self.config_change_callbacks:
                    await self._notify_module_config_change(module, self.default_config["system"].get(module, {}))
                    
            return success
            
        except Exception as e:
            self.logger.error(f"重置配置失败: {e}")
            return False
            
    async def _notify_module_config_change(self, module: str, config_data: Dict[str, Any]):
        if module in self.config_change_callbacks:
            for callback in self.config_change_callbacks[module]:
                if callable(callback):
                    await callback(module, None, config_data)
                    
    async def shutdown(self):
        self.logger.info("配置管理器已关闭")