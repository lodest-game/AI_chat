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
                        "你所处的环境是即时聊天平台，请关注当前问题，历史问题作为聊天背景，面对即时聊天，很有可能面对多次同类型的请求，请正确的判断并回复，不应采用复述之前回答的方式。",
                        "接受的信息格式中，“发言人”表示发言人昵称。“发言内容”表示具体的用户讨论信息。",
                        "对于总结群聊信息等整合要求，无需判断调用工具，仅需总结上下文中讨论信息即可。历史聊天记录已经成为上下文中的一部分。",
                        "你可以使用工具来帮助用户解决问题。"
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