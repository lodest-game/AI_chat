# tool_manager.py
#!/usr/bin/env python3

import importlib
import inspect
import logging
import json
import asyncio
import sys
from pathlib import Path
import re

class ToolConfig:
    def __init__(self, name, timeout=30.0, max_retries=1, enabled=True):
        self.name = name
        self.timeout = timeout
        self.max_retries = max_retries
        self.enabled = enabled

class ToolManager:
    def __init__(self, tools_service_dir):
        self.logger = logging.getLogger(__name__)
        self.tools_service_dir = Path(tools_service_dir)
        
        self.tools_registry = {}
        self.tool_configs = {}
        self.tool_definitions_cache = []
        self.loaded_modules = {}
        
        self.config = None
        self.lock = asyncio.Lock()
        self.context_manager = None
        
    def set_context_manager(self, context_manager):
        self.context_manager = context_manager
        
    async def initialize(self, config):
        self.config = config
        await self._ensure_directories()
        await self._ensure_required_services()
        await self._scan_and_register_tools()
        await self._load_tool_configs()
        
    async def _ensure_required_services(self):
        services_to_ensure = ["prompt_service.py"]
        
        for service_file in services_to_ensure:
            service_path = self.tools_service_dir / service_file
            if not service_path.exists():
                await self._create_default_service(service_path, service_file)
                
    async def _create_default_service(self, service_path, service_name):
        if service_name == "prompt_service.py":
            content = '''#!/usr/bin/env python3

import json
import logging

_context_manager = None
_logger = logging.getLogger(__name__)

def set_context_manager(context_manager):
    global _context_manager
    _context_manager = context_manager

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

TOOL_CONFIGS = {
    "prompt_service_view_prompt": {"timeout": 10.0},
    "prompt_service_set_prompt": {"timeout": 15.0},
    "prompt_service_delete_prompt": {"timeout": 10.0}
}

async def prompt_service_view_prompt(chat_id):
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


async def prompt_service_set_prompt(chat_id, prompt_content):
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


async def prompt_service_delete_prompt(chat_id):
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

TOOL_HANDLERS = {
    "prompt_service_view_prompt": prompt_service_view_prompt,
    "prompt_service_set_prompt": prompt_service_set_prompt,
    "prompt_service_delete_prompt": prompt_service_delete_prompt
}
'''
            with open(service_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
    async def _ensure_directories(self):
        if not self.tools_service_dir.exists():
            self.tools_service_dir.mkdir(parents=True, exist_ok=True)
            
    async def _load_tool_configs(self):
        default_timeout = self.config.get("system", {}).get("tool_manager", {}).get("default_tool_timeout", 30.0)
        
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
            
    async def _register_tool_module(self, tool_file):
        module_name = tool_file.stem
        
        original_sys_path = sys.path.copy()
        try:
            sys.path.insert(0, str(tool_file.parent))
            
            from importlib.util import spec_from_file_location, module_from_spec
            
            spec = spec_from_file_location(module_name, str(tool_file))
            if spec is None:
                return
                
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            
            self.loaded_modules[module_name] = module
            
            if hasattr(module, 'set_context_manager') and self.context_manager:
                try:
                    module.set_context_manager(self.context_manager)
                    self.logger.debug(f"已为模块 {module_name} 注入上下文管理器")
                except Exception as e:
                    self.logger.error(f"注入上下文管理器到模块 {module_name} 失败: {e}")
            
            if not hasattr(module, 'TOOL_DEFINITIONS'):
                self.logger.warning(f"模块 {module_name} 没有TOOL_DEFINITIONS属性，跳过")
                return
                
            tool_definitions = module.TOOL_DEFINITIONS
            tool_handlers = getattr(module, 'TOOL_HANDLERS', {})
            
            for tool_def in tool_definitions:
                await self._register_tool_from_definition(module, tool_def, tool_handlers)
                
        except Exception as e:
            self.logger.error(f"导入并注册工具模块失败 {module_name}: {e}")
        finally:
            sys.path = original_sys_path.copy()
                
    async def _register_tool_from_definition(self, module, tool_def, tool_handlers):
        try:
            tool_info = tool_def.get("function", {})
            tool_name = tool_info.get("name")
            
            if not tool_name:
                self.logger.warning("工具定义中没有name字段，跳过")
                return
                
            handler = None
            
            if tool_name in tool_handlers:
                handler = tool_handlers[tool_name]
            elif hasattr(module, tool_name):
                handler = getattr(module, tool_name)
            else:
                self.logger.warning(f"工具 {tool_name} 未找到对应的处理函数")
                return
                
            if not asyncio.iscoroutinefunction(handler):
                self.logger.warning(f"工具处理函数 {tool_name} 不是异步函数")
                return
                
            self.tools_registry[tool_name] = {
                "definition": tool_def,
                "handler": handler,
                "module": module.__name__
            }
            
            self.logger.debug(f"已注册工具: {tool_name}")
            
        except Exception as e:
            self.logger.error(f"注册工具失败: {e}")
            
    async def inject_context_to_modules(self):
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
        self.tool_definitions_cache = [
            tool_info["definition"]
            for tool_info in self.tools_registry.values()
        ]
        
    def get_tool_definitions(self):
        return self.tool_definitions_cache.copy()
        
    async def execute_tool_with_timeout(self, tool_name, arguments, chat_id=None, session_id=None):
        if tool_name not in self.tools_registry:
            return f"工具不存在: {tool_name}"
            
        config = self.tool_configs.get(tool_name, ToolConfig(name=tool_name))
        if not config.enabled:
            return f"工具已禁用: {tool_name}"
            
        try:
            tool_info = self.tools_registry[tool_name]
            handler = tool_info["handler"]
            
            params = arguments.copy()
            
            sig = inspect.signature(handler)
            param_names = list(sig.parameters.keys())
            
            if 'chat_id' in param_names and chat_id:
                params['chat_id'] = chat_id
            if 'session_id' in param_names and session_id:
                params['session_id'] = session_id
                
            try:
                result = await asyncio.wait_for(
                    handler(**params),
                    timeout=config.timeout
                )
                
                return str(result)
                    
            except asyncio.TimeoutError:
                self.logger.warning(f"工具执行超时: {tool_name} (超时时间: {config.timeout}s)")
                return f"工具执行超时 (超时时间: {config.timeout}s)"
                
        except Exception as e:
            self.logger.error(f"执行工具失败 {tool_name}: {e}")
            return f"工具执行失败: {str(e)}"
            
    async def reload_tools(self):
        try:
            await self._scan_and_register_tools()
            await self._load_tool_configs()
            
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
            
    def get_tool_info(self, tool_name):
        if tool_name not in self.tools_registry:
            return None
            
        tool_info = self.tools_registry[tool_name]
        handler = tool_info["handler"]
        
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
        
    def list_tools(self):
        return list(self.tools_registry.keys())
        
    def get_registered_tools_count(self):
        return len(self.tools_registry)
        
    async def update_tool_config(self, tool_name, config_data):
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
            
    async def get_tool_config(self, tool_name):
        if tool_name not in self.tools_registry:
            return None
            
        config = self.tool_configs.get(tool_name, ToolConfig(name=tool_name))
        
        return {
            "name": config.name,
            "timeout": config.timeout,
            "max_retries": config.max_retries,
            "enabled": config.enabled
        }