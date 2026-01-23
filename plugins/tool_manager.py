#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tool_manager.py - 直接注册工具定义的异步工具管理器
"""

import os
import importlib
import inspect
import logging
import json
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
import re


class ToolManager:
    def __init__(self, tools_service_dir: Path):
        self.logger = logging.getLogger(__name__)
        self.tools_service_dir = Path(tools_service_dir)
        
        # 工具注册表：工具名 -> (工具定义, 处理函数)
        self.tools_registry = {}
        
        # 工具定义缓存
        self.tool_definitions_cache = []
        
        self.config = None
        self.lock = asyncio.Lock()
        self.context_manager = None  # 添加这个属性
        
    def set_context_manager(self, context_manager):
        """设置上下文管理器（保持与原有代码兼容）"""
        self.context_manager = context_manager
        
    async def initialize(self, config: Dict[str, Any]):
        self.config = config
        await self._ensure_directories()
        await self._ensure_required_services()
        await self._scan_and_register_tools()
        
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
prompt_service.py - 提示词管理服务（带工具定义）
"""

import json
from typing import Dict, Any

# ==================== 工具定义 ====================
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "prompt_service_view_prompt",
            "description": "查看当前对话的专属提示词。仅用于当前请求明确是查看提示词内容的请求，才会调用。",
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
            "description": "设置当前对话的专属提示词。仅用于当前请求明确是设定提示词内容的请求，才会调用。",
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
            "description": "删除当前对话的专属提示词。仅用于当前请求明确是删除提示词内容的请求，才会调用。",
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

# ==================== 工具处理函数 ====================

async def prompt_service_view_prompt(chat_id: str) -> Dict[str, Any]:
    """
    查看专属提示词
    
    Args:
        chat_id: 对话ID
        
    Returns:
        提示词信息
    """
    # 实际查询逻辑由task_manager中的context_manager处理
    return {
        "success": True,
        "chat_id": chat_id,
        "action": "view_prompt",
        "status": "awaiting_context_query",
        "note": "这是一个查询操作，请等待上下文管理器返回实际数据"
    }


async def prompt_service_set_prompt(chat_id: str, prompt_content: str) -> Dict[str, Any]:
    """
    设置专属提示词
    
    Args:
        chat_id: 对话ID
        prompt_content: 提示词内容
        
    Returns:
        设置结果
    """
    return {
        "success": True,
        "chat_id": chat_id,
        "prompt_content": prompt_content,
        "action": "set_prompt",
        "status": "configuration_requested",
        "user_feedback": f"已收到设置提示词的请求：{prompt_content[:50]}...",
        "ai_instruction": "✅ 提示词设置请求已提交。请继续对话，无需进一步工具调用。",
        "next_step": "context_manager_will_handle"
    }


async def prompt_service_delete_prompt(chat_id: str) -> Dict[str, Any]:
    """
    删除专属提示词
    
    Args:
        chat_id: 对话ID
        
    Returns:
        删除结果
    """
    return {
        "success": True,
        "chat_id": chat_id,
        "action": "delete_prompt",
        "status": "deletion_requested",
        "user_feedback": "已收到删除提示词的请求",
        "ai_instruction": "✅ 提示词删除请求已提交。请继续对话，无需进一步工具调用。"
    }

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
            
    async def _scan_and_register_tools(self):
        """扫描并注册所有工具"""
        async with self.lock:
            self.tools_registry.clear()
            self.tool_definitions_cache.clear()
            
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
        import sys
        import importlib.util
        
        sys.path.insert(0, str(tool_file.parent))
        
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(tool_file))
            if spec is None:
                return
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
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
        finally:
            if str(tool_file.parent) in sys.path:
                sys.path.remove(str(tool_file.parent))
                
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
            
    def _generate_tool_definitions_cache(self):
        """生成工具定义缓存"""
        self.tool_definitions_cache = [
            tool_info["definition"]
            for tool_info in self.tools_registry.values()
        ]
        
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """获取所有工具定义"""
        return self.tool_definitions_cache.copy()
        
    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any],
                          session_id: str = None, chat_id: str = None) -> Dict[str, Any]:
        """执行指定工具"""
        if tool_name not in self.tools_registry:
            return {"success": False, "error": f"工具不存在: {tool_name}"}
            
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
                
            # 执行工具
            result = await handler(**params)
            
            if not isinstance(result, dict):
                result = {"result": result}
                
            if "success" not in result:
                result["success"] = True
                
            return result
            
        except Exception as e:
            self.logger.error(f"执行工具失败 {tool_name}: {e}", exc_info=True)
            return {"success": False, "error": str(e), "tool_name": tool_name}
            
    async def reload_tools(self) -> Dict[str, Any]:
        """重新加载所有工具"""
        try:
            await self._scan_and_register_tools()
            
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
        
        return {
            "name": tool_name,
            "definition": tool_info["definition"],
            "handler_name": handler.__name__,
            "module": tool_info["module"],
            "is_async": asyncio.iscoroutinefunction(handler)
        }
        
    def list_tools(self) -> List[str]:
        """列出所有已注册的工具"""
        return list(self.tools_registry.keys())
        
    def get_registered_tools_count(self) -> int:
        """获取已注册工具数量"""
        return len(self.tools_registry)