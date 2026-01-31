#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tool_manager.py - 重构后的异步工具管理器
主要改进：
1. 移除自定义协议层，工具返回原始content
2. 添加工具超时配置
3. 支持工具超时控制
"""

import os
import importlib
import inspect
import logging
import json
import asyncio
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
import re
from dataclasses import dataclass


@dataclass
class ToolConfig:
    """工具配置"""
    name: str
    timeout: float = 30.0  # 默认30秒超时
    max_retries: int = 1
    enabled: bool = True


class ToolManager:
    def __init__(self, tools_service_dir: Path):
        self.logger = logging.getLogger(__name__)
        self.tools_service_dir = Path(tools_service_dir)
        
        # 工具注册表：工具名 -> (工具定义, 处理函数, 配置)
        self.tools_registry = {}
        
        # 工具配置缓存
        self.tool_configs = {}
        
        # 工具定义缓存
        self.tool_definitions_cache = []
        
        # 已加载的模块
        self.loaded_modules = {}
        
        self.config = None
        self.lock = asyncio.Lock()
        self.context_manager = None
        
    def set_context_manager(self, context_manager):
        """设置上下文管理器引用"""
        self.context_manager = context_manager
        
    async def initialize(self, config: Dict[str, Any]):
        self.config = config
        await self._ensure_directories()
        await self._ensure_required_services()
        await self._scan_and_register_tools()
        await self._load_tool_configs()
        
    async def _ensure_required_services(self):
        """确保必要的工具服务存在"""
        services_to_ensure = ["prompt_service.py"]
        
        for service_file in services_to_ensure:
            service_path = self.tools_service_dir / service_file
            if not service_path.exists():
                await self._create_default_service(service_path, service_file)
                
    async def _create_default_service(self, service_path: Path, service_name: str):
        """创建默认的工具服务文件"""
        if service_name == "prompt_service.py":
            content = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompt_service.py - 提示词管理服务
"""

import json
import logging
from typing import Dict, Any

# ==================== 上下文管理器引用 ====================
_context_manager = None
_logger = logging.getLogger(__name__)

def set_context_manager(context_manager):
    """设置上下文管理器引用"""
    global _context_manager
    _context_manager = context_manager

# ==================== 工具定义 ====================
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "prompt_service_view_prompt",
            "description": "查看当前对话的专属提示词",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "对话ID"
                    }
                },
                "required": ["chat_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "prompt_service_set_prompt",
            "description": "设置当前对话的专属提示词",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "对话ID"
                    },
                    "prompt_content": {
                        "type": "string",
                        "description": "要设置的提示词内容"
                    }
                },
                "required": ["chat_id", "prompt_content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "prompt_service_delete_prompt",
            "description": "删除当前对话的专属提示词",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "对话ID"
                    }
                },
                "required": ["chat_id"]
            }
        }
    }
]

# ==================== 工具配置 ====================
# 每个工具可以有自己的超时配置（单位：秒）
TOOL_CONFIGS = {
    "prompt_service_view_prompt": {"timeout": 10.0},
    "prompt_service_set_prompt": {"timeout": 15.0},
    "prompt_service_delete_prompt": {"timeout": 10.0}
}

# ==================== 工具处理函数 ====================

async def prompt_service_view_prompt(chat_id: str) -> str:
    """
    查看专属提示词 - 返回原始content
    """
    if not _context_manager:
        return "上下文管理器未初始化"
    
    try:
        result = await _context_manager.get_custom_prompt(chat_id)
        
        if result.get("success"):
            has_custom = result.get("has_custom_prompt", False)
            custom_prompt = result.get("custom_prompt", "")
            
            if has_custom:
                return f"当前对话的专属提示词：{custom_prompt}"
            else:
                return "当前对话没有设置专属提示词，使用默认核心提示词"
        else:
            return f"查询失败：{result.get('error')}"
            
    except Exception as e:
        _logger.error(f"查看提示词失败: {e}")
        return f"查看提示词失败: {str(e)}"


async def prompt_service_set_prompt(chat_id: str, prompt_content: str) -> str:
    """
    设置专属提示词 - 返回原始content
    """
    if not _context_manager:
        return "上下文管理器未初始化"
    
    if not prompt_content or not prompt_content.strip():
        return "提示词内容不能为空"
    
    if len(prompt_content) > 5000:
        return "提示词内容过长，请限制在5000字符内"
    
    try:
        result = await _context_manager.update_custom_prompt(
            chat_id=chat_id,
            prompt_content=prompt_content
        )
        
        if result.get("success"):
            return f"专属提示词设置成功：{prompt_content[:100]}{'...' if len(prompt_content) > 100 else ''}"
        else:
            return f"设置失败：{result.get('error')}"
            
    except Exception as e:
        _logger.error(f"设置提示词失败: {e}")
        return f"设置提示词失败: {str(e)}"


async def prompt_service_delete_prompt(chat_id: str) -> str:
    """
    删除专属提示词 - 返回原始content
    """
    if not _context_manager:
        return "上下文管理器未初始化"
    
    try:
        result = await _context_manager.delete_custom_prompt(chat_id)
        
        if result.get("success"):
            return "专属提示词已删除"
        else:
            return f"删除失败：{result.get('error')}"
            
    except Exception as e:
        _logger.error(f"删除提示词失败: {e}")
        return f"删除提示词失败: {str(e)}"

# ==================== 工具注册映射 ====================
TOOL_HANDLERS = {
    "prompt_service_view_prompt": prompt_service_view_prompt,
    "prompt_service_set_prompt": prompt_service_set_prompt,
    "prompt_service_delete_prompt": prompt_service_delete_prompt
}
'''
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._write_file_sync(service_path, content)
            )
            
    def _write_file_sync(self, file_path: Path, content: str):
        """同步写入文件"""
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
    async def _ensure_directories(self):
        """确保目录存在"""
        if not self.tools_service_dir.exists():
            self.tools_service_dir.mkdir(parents=True, exist_ok=True)
            
    async def _load_tool_configs(self):
        """加载工具配置"""
        # 首先设置默认配置
        default_timeout = self.config.get("system", {}).get("tool_manager", {}).get("default_tool_timeout", 30.0)
        
        # 从各个工具模块加载配置
        for module_name, module in self.loaded_modules.items():
            if hasattr(module, 'TOOL_CONFIGS'):
                tool_configs = module.TOOL_CONFIGS
                for tool_name, config in tool_configs.items():
                    self.tool_configs[tool_name] = ToolConfig(
                        name=tool_name,
                        timeout=config.get("timeout", default_timeout),
                        max_retries=config.get("max_retries", 1),
                        enabled=config.get("enabled", True)
                    )
                    
    async def _scan_and_register_tools(self):
        """扫描并注册所有工具"""
        async with self.lock:
            self.tools_registry.clear()
            self.tool_definitions_cache.clear()
            self.loaded_modules.clear()
            
            tool_files = list(self.tools_service_dir.glob("*.py"))
            
            for tool_file in tool_files:
                try:
                    await self._register_tool_module(tool_file)
                except Exception as e:
                    self.logger.error(f"注册工具模块失败 {tool_file.name}: {e}")
                    
            self._generate_tool_definitions_cache()
            
    async def _register_tool_module(self, tool_file: Path):
        """注册单个工具模块"""
        module_name = tool_file.stem
        
        # 临时添加路径到sys.path
        original_sys_path = sys.path.copy()
        try:
            sys.path.insert(0, str(tool_file.parent))
            
            # 使用正确的importlib方式
            from importlib.util import spec_from_file_location, module_from_spec
            
            spec = spec_from_file_location(module_name, str(tool_file))
            if spec is None:
                return
                
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 保存模块引用
            self.loaded_modules[module_name] = module
            
            # 如果有set_context_manager函数，注入上下文管理器
            if hasattr(module, 'set_context_manager') and self.context_manager:
                try:
                    module.set_context_manager(self.context_manager)
                    self.logger.debug(f"已为模块 {module_name} 注入上下文管理器")
                except Exception as e:
                    self.logger.error(f"注入上下文管理器到模块 {module_name} 失败: {e}")
            
            # 检查模块是否有TOOL_DEFINITIONS属性
            if not hasattr(module, 'TOOL_DEFINITIONS'):
                self.logger.warning(f"模块 {module_name} 没有TOOL_DEFINITIONS属性，跳过")
                return
                
            # 获取工具定义
            tool_definitions = module.TOOL_DEFINITIONS
            
            # 获取工具处理函数映射
            tool_handlers = getattr(module, 'TOOL_HANDLERS', {})
            
            # 注册每个工具
            for tool_def in tool_definitions:
                await self._register_tool_from_definition(module, tool_def, tool_handlers)
                
        except Exception as e:
            self.logger.error(f"导入并注册工具模块失败 {module_name}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
        finally:
            # 恢复原始sys.path
            sys.path = original_sys_path.copy()
                
    async def _register_tool_from_definition(self, module, tool_def: Dict[str, Any], 
                                            tool_handlers: Dict[str, Callable]):
        """从工具定义注册单个工具"""
        try:
            # 提取工具信息
            tool_info = tool_def.get("function", {})
            tool_name = tool_info.get("name")
            
            if not tool_name:
                self.logger.warning("工具定义中没有name字段，跳过")
                return
                
            # 查找处理函数
            handler = None
            
            # 1. 首先从TOOL_HANDLERS映射中查找
            if tool_name in tool_handlers:
                handler = tool_handlers[tool_name]
            # 2. 然后在模块中查找同名函数
            elif hasattr(module, tool_name):
                handler = getattr(module, tool_name)
            else:
                self.logger.warning(f"工具 {tool_name} 未找到对应的处理函数")
                return
                
            # 验证处理函数是异步的
            if not asyncio.iscoroutinefunction(handler):
                self.logger.warning(f"工具处理函数 {tool_name} 不是异步函数")
                return
                
            # 注册工具
            self.tools_registry[tool_name] = {
                "definition": tool_def,
                "handler": handler,
                "module": module.__name__
            }
            
            self.logger.debug(f"已注册工具: {tool_name}")
            
        except Exception as e:
            self.logger.error(f"注册工具失败: {e}")
            
    async def inject_context_to_modules(self):
        """为所有已加载的模块注入上下文管理器"""
        if not self.context_manager:
            return
            
        async with self.lock:
            for module_name, module in self.loaded_modules.items():
                try:
                    if hasattr(module, 'set_context_manager'):
                        module.set_context_manager(self.context_manager)
                        self.logger.debug(f"已为模块 {module_name} 注入上下文管理器")
                except Exception as e:
                    self.logger.error(f"注入上下文管理器到模块 {module_name} 失败: {e}")
    
    def _generate_tool_definitions_cache(self):
        """生成工具定义缓存"""
        self.tool_definitions_cache = [
            tool_info["definition"]
            for tool_info in self.tools_registry.values()
        ]
        
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """获取所有工具定义"""
        return self.tool_definitions_cache.copy()
        
    async def execute_tool_with_timeout(self, tool_name: str, arguments: Dict[str, Any],
                                      chat_id: str = None, session_id: str = None) -> str:
        """执行指定工具（带超时控制）"""
        if tool_name not in self.tools_registry:
            return f"工具不存在: {tool_name}"
            
        # 获取工具配置
        config = self.tool_configs.get(tool_name, ToolConfig(name=tool_name))
        if not config.enabled:
            return f"工具已禁用: {tool_name}"
            
        try:
            tool_info = self.tools_registry[tool_name]
            handler = tool_info["handler"]
            
            # 准备参数
            params = arguments.copy()
            
            # 自动添加chat_id和session_id（如果处理函数需要）
            sig = inspect.signature(handler)
            param_names = list(sig.parameters.keys())
            
            if 'chat_id' in param_names and chat_id:
                params['chat_id'] = chat_id
            if 'session_id' in param_names and session_id:
                params['session_id'] = session_id
                
            # 执行工具（带超时）
            try:
                result = await asyncio.wait_for(
                    handler(**params),
                    timeout=config.timeout
                )
                
                # 工具返回原始content（字符串）
                return str(result)
                    
            except asyncio.TimeoutError:
                self.logger.warning(f"工具执行超时: {tool_name} (超时时间: {config.timeout}s)")
                return f"工具执行超时 (超时时间: {config.timeout}s)"
                
        except Exception as e:
            self.logger.error(f"执行工具失败 {tool_name}: {e}", exc_info=True)
            return f"工具执行失败: {str(e)}"
            
    async def reload_tools(self) -> Dict[str, Any]:
        """重新加载所有工具"""
        try:
            await self._scan_and_register_tools()
            await self._load_tool_configs()
            
            # 重新注入上下文管理器
            if self.context_manager:
                await self.inject_context_to_modules()
            
            return {
                "success": True,
                "message": f"工具系统已重载，当前注册 {len(self.tools_registry)} 个工具",
                "tool_count": len(self.tools_registry)
            }
            
        except Exception as e:
            self.logger.error(f"重载工具失败: {e}")
            return {"success": False, "error": str(e)}
            
    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """获取工具详细信息"""
        if tool_name not in self.tools_registry:
            return None
            
        tool_info = self.tools_registry[tool_name]
        handler = tool_info["handler"]
        
        # 获取工具配置
        config = self.tool_configs.get(tool_name, ToolConfig(name=tool_name))
        
        return {
            "name": tool_name,
            "definition": tool_info["definition"],
            "handler_name": handler.__name__,
            "module": tool_info["module"],
            "config": {
                "timeout": config.timeout,
                "max_retries": config.max_retries,
                "enabled": config.enabled
            },
            "is_async": asyncio.iscoroutinefunction(handler)
        }
        
    def list_tools(self) -> List[str]:
        """列出所有已注册的工具"""
        return list(self.tools_registry.keys())
        
    def get_registered_tools_count(self) -> int:
        """获取已注册工具数量"""
        return len(self.tools_registry)
        
    async def update_tool_config(self, tool_name: str, config_data: Dict[str, Any]) -> bool:
        """更新工具配置"""
        if tool_name not in self.tools_registry:
            return False
            
        try:
            if tool_name not in self.tool_configs:
                self.tool_configs[tool_name] = ToolConfig(name=tool_name)
                
            config = self.tool_configs[tool_name]
            
            if "timeout" in config_data:
                config.timeout = float(config_data["timeout"])
            if "max_retries" in config_data:
                config.max_retries = int(config_data["max_retries"])
            if "enabled" in config_data:
                config.enabled = bool(config_data["enabled"])
                
            return True
            
        except Exception as e:
            self.logger.error(f"更新工具配置失败 {tool_name}: {e}")
            return False
            
    async def get_tool_config(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """获取工具配置"""
        if tool_name not in self.tools_registry:
            return None
            
        config = self.tool_configs.get(tool_name, ToolConfig(name=tool_name))
        
        return {
            "name": config.name,
            "timeout": config.timeout,
            "max_retries": config.max_retries,
            "enabled": config.enabled
        }