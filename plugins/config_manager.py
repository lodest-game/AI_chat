#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config_manager.py - 系统配置管理器
统一管理系统核心配置，确保配置一致性和完整性
"""

import os
import json
import logging
import copy
from pathlib import Path
from typing import Dict, Any, Optional


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, plugins_dir: Path):
        """
        初始化配置管理器
        
        Args:
            plugins_dir: 插件目录
        """
        self.logger = logging.getLogger(__name__)
        
        # 目录配置
        self.plugins_dir = Path(plugins_dir)
        self.config_file = self.plugins_dir / "system.json"
        
        # 配置数据
        self.config = {}
        
        # 默认配置
        self.default_config = self._create_default_config()
        
        # 配置变更回调
        self.config_change_callbacks = {}
        
    def _create_default_config(self) -> Dict[str, Any]:
        """创建默认配置"""
        return {
            "system": {
                "context_manager": {
                    "default_model": "local_model",
                    "chat_mode": {
                        "LLM": [],
                        "MLLM": ["local_model"]
                    },
                    "default_tools_call": True,
                    "model": {
                        "max_tokens": 64000,
                        "temperature": 0.7,
                        "stream": False
                    },
                    "core_prompt": [
                        "你是智能助手，请根据用户的问题提供准确、有用的回答。",
                        "你可以使用工具来帮助用户解决问题。"
                    ],
                    "max_user_messages_per_chat": 20,  # 每个对话最大用户消息数
                    "virtual_reply_enabled": True,
                    "virtual_reply_text": "已跳过此信息",
                    "cache_inactive_unload_seconds": 1800,
                },
                "rules_manager": {
                    "mode": "wait"  # all:完全并行，wait:局部并行
                },
                "port_manager": {
                    "reconnect_interval": 10,
                    "max_reconnect_attempts": 3
                },
                "essentials_manager": {
                    "enable_model_management": True,
                    "enable_prompt_management": True,
                    "enable_tool_management": True,
                    "permission_required": True,
                    "admin_chats": ["qq_private_1308213863"]
                },
                "session_manager": {
                    "session_timeout_minutes": 10,
                    "max_sessions": 100
                },
                "tool_manager": {
                    "auto_discovery": True,
                    "generate_stubs": True
                }
            }
        }
        
    async def initialize(self) -> Dict[str, Any]:
        """
        初始化配置管理器
        
        Returns:
            加载的配置数据
        """
        self.logger.info("初始化配置管理器...")
        
        # 确保目录存在
        self.plugins_dir.mkdir(exist_ok=True)
        
        # 加载或创建配置
        if self.config_file.exists():
            self.config = await self._load_config()
        else:
            self.config = self.default_config
            await self._save_config()
            
        # 验证配置
        if not self._validate_config():
            self.logger.warning("配置验证失败，使用默认配置")
            self.config = self.default_config
            await self._save_config()
            
        self.logger.info(f"配置加载完成，配置文件: {self.config_file}")
        
        return self.config
        
    async def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
            self.logger.info(f"配置文件已加载: {self.config_file}")
            return config
            
        except json.JSONDecodeError as e:
            self.logger.error(f"配置文件JSON格式错误: {e}")
            raise
            
        except Exception as e:
            self.logger.error(f"加载配置文件失败: {e}")
            raise
            
    async def _save_config(self) -> bool:
        """保存配置文件"""
        try:
            # 确保目录存在
            self.config_file.parent.mkdir(exist_ok=True)
            
            # 保存配置文件
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
                
            self.logger.info(f"配置文件已保存: {self.config_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
            return False
            
    def _validate_config(self) -> bool:
        """验证配置完整性"""
        if not self.config:
            self.logger.error("配置为空")
            return False
            
        # 检查基本结构
        if "system" not in self.config:
            self.logger.error("配置缺少system根节点")
            return False
            
        system_config = self.config["system"]
        
        # 验证各模块配置
        required_modules = [
            "context_manager",
            "rules_manager",
            "port_manager",
            "essentials_manager",
            "session_manager"
        ]
        
        for module in required_modules:
            if module not in system_config:
                self.logger.warning(f"配置缺少{module}模块，将使用默认值")
                system_config[module] = self.default_config["system"].get(module, {})
                
        # 验证context_manager
        context_config = system_config.get("context_manager", {})
        if not context_config:
            self.logger.warning("context_manager配置为空，使用默认值")
            system_config["context_manager"] = self.default_config["system"]["context_manager"]
        else:
            # 验证必要字段
            required_fields = [
                "default_model",
                "chat_mode",
                "default_tools_call",
                "model",
                "core_prompt",
                "max_user_messages_per_chat",  # 修改字段名
                "cache_inactive_unload_seconds"
            ]
            
            for field in required_fields:
                if field not in context_config:
                    self.logger.warning(f"context_manager缺少{field}，使用默认值")
                    context_config[field] = self.default_config["system"]["context_manager"].get(field)
                    
        # 验证rules_manager
        rules_config = system_config.get("rules_manager", {})
        if not rules_config or "mode" not in rules_config:
            self.logger.warning("rules_manager配置不完整，使用默认值")
            system_config["rules_manager"] = self.default_config["system"]["rules_manager"]
            
        # 验证port_manager
        port_config = system_config.get("port_manager", {})
        if not port_config:
            self.logger.warning("port_manager配置为空，使用默认值")
            system_config["port_manager"] = self.default_config["system"]["port_manager"]
            
        # 验证essentials_manager
        essentials_config = system_config.get("essentials_manager", {})
        if not essentials_config:
            self.logger.warning("essentials_manager配置为空，使用默认值")
            system_config["essentials_manager"] = self.default_config["system"]["essentials_manager"]
            
        # 验证session_manager
        session_config = system_config.get("session_manager", {})
        if not session_config:
            self.logger.warning("session_manager配置为空，使用默认值")
            system_config["session_manager"] = self.default_config["system"]["session_manager"]
            
        # 验证tool_manager
        tool_config = system_config.get("tool_manager", {})
        if not tool_config:
            self.logger.warning("tool_manager配置为空，使用默认值")
            system_config["tool_manager"] = self.default_config["system"]["tool_manager"]
            
        return True
        
    def get_config(self, module: str = None, key: str = None) -> Any:
        """
        获取配置
        
        Args:
            module: 模块名（如"context_manager"）
            key: 配置键名
            
        Returns:
            配置值
        """
        try:
            if module is None and key is None:
                return copy.deepcopy(self.config)
                
            if module is not None and key is None:
                if "system" in self.config and module in self.config["system"]:
                    return copy.deepcopy(self.config["system"][module])
                else:
                    return None
                    
            if module is not None and key is not None:
                if ("system" in self.config and 
                    module in self.config["system"] and 
                    key in self.config["system"][module]):
                    return copy.deepcopy(self.config["system"][module][key])
                else:
                    return None
                    
        except Exception as e:
            self.logger.error(f"获取配置失败: module={module}, key={key}, error={e}")
            return None
            
        return None
        
    async def update_config(self, module: str, key: str, value: Any) -> bool:
        """
        更新配置
        
        Args:
            module: 模块名
            key: 配置键名
            value: 配置值
            
        Returns:
            是否成功
        """
        try:
            # 检查模块是否存在
            if "system" not in self.config:
                self.config["system"] = {}
                
            if module not in self.config["system"]:
                self.config["system"][module] = {}
                
            # 更新配置
            self.config["system"][module][key] = value
            
            # 保存配置
            success = await self._save_config()
            
            if success:
                # 触发配置变更回调
                await self._notify_config_change(module, key, value)
                
            return success
            
        except Exception as e:
            self.logger.error(f"更新配置失败: module={module}, key={key}, value={value}, error={e}")
            return False
            
    async def update_module_config(self, module: str, config_data: Dict[str, Any]) -> bool:
        """
        更新整个模块配置
        
        Args:
            module: 模块名
            config_data: 配置数据
            
        Returns:
            是否成功
        """
        try:
            # 检查模块是否存在
            if "system" not in self.config:
                self.config["system"] = {}
                
            # 更新配置
            self.config["system"][module] = config_data
            
            # 保存配置
            success = await self._save_config()
            
            if success:
                # 触发配置变更回调
                await self._notify_module_config_change(module, config_data)
                
            return success
            
        except Exception as e:
            self.logger.error(f"更新模块配置失败: module={module}, error={e}")
            return False
            
    def register_config_change_callback(self, module: str, callback):
        """
        注册配置变更回调
        
        Args:
            module: 模块名
            callback: 回调函数
        """
        if module not in self.config_change_callbacks:
            self.config_change_callbacks[module] = []
            
        self.config_change_callbacks[module].append(callback)
        
    async def _notify_config_change(self, module: str, key: str, value: Any):
        """通知配置变更"""
        if module in self.config_change_callbacks:
            for callback in self.config_change_callbacks[module]:
                try:
                    if callable(callback):
                        await callback(module, key, value)
                except Exception as e:
                    self.logger.error(f"配置变更回调执行失败: module={module}, error={e}")
                    
    async def _notify_module_config_change(self, module: str, config_data: Dict[str, Any]):
        """通知模块配置变更"""
        if module in self.config_change_callbacks:
            for callback in self.config_change_callbacks[module]:
                try:
                    if callable(callback):
                        await callback(module, None, config_data)  # key为None表示整个模块
                except Exception as e:
                    self.logger.error(f"模块配置变更回调执行失败: module={module}, error={e}")
                    
    def get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return copy.deepcopy(self.default_config)
        
    async def reset_to_defaults(self) -> bool:
        """重置为默认配置"""
        try:
            self.config = self.default_config
            success = await self._save_config()
            
            if success:
                # 通知所有模块配置变更
                for module in self.config_change_callbacks:
                    await self._notify_module_config_change(module, self.default_config["system"].get(module, {}))
                    
            return success
            
        except Exception as e:
            self.logger.error(f"重置配置失败: {e}")
            return False
            
    def get_config_file_path(self) -> Path:
        """获取配置文件路径"""
        return self.config_file
        
    def get_config_summary(self) -> Dict[str, Any]:
        """获取配置摘要"""
        summary = {
            "config_file": str(self.config_file),
            "config_size": len(json.dumps(self.config, ensure_ascii=False)),
            "modules": {}
        }
        
        if "system" in self.config:
            for module, config in self.config["system"].items():
                summary["modules"][module] = {
                    "keys": list(config.keys()),
                    "config_size": len(json.dumps(config, ensure_ascii=False))
                }
                
        return summary
        
    async def reload_config(self) -> Dict[str, Any]:
        """
        重新加载配置
        
        Returns:
            重新加载的配置
        """
        try:
            if self.config_file.exists():
                loaded_config = await self._load_config()
                
                # 合并现有配置和新加载的配置
                # 这里使用新配置覆盖现有配置
                self.config = loaded_config
                
                # 验证配置
                self._validate_config()
                
                self.logger.info("配置已重新加载")
                
                # 通知所有模块配置变更
                for module in self.config_change_callbacks:
                    if "system" in self.config and module in self.config["system"]:
                        await self._notify_module_config_change(module, self.config["system"][module])
                        
            else:
                self.logger.warning("配置文件不存在，无法重新加载")
                
            return self.config
            
        except Exception as e:
            self.logger.error(f"重新加载配置失败: {e}")
            return self.config
            
    async def shutdown(self):
        """关闭配置管理器"""
        self.logger.info("配置管理器已关闭")