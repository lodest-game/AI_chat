#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tool_manager.py - 完全异步工具函数管理器
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
        self.tools_registry = {}
        self.tool_functions = {}
        self.tool_definitions_cache = []
        self.config = None
        self.lock = asyncio.Lock()
        self.context_manager = None
        
    async def initialize(self, config: Dict[str, Any]):
        self.config = config
        await self._ensure_directories()
        await self._ensure_prompt_service_exists()
        await self._scan_and_register_tools()
        
    async def _ensure_prompt_service_exists(self):
        prompt_service_path = self.tools_service_dir / "prompt_service.py"
        
        if not prompt_service_path.exists():
            prompt_service_content = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompt_service - 提示词管理工具（优化返回格式版）
不改变原有业务逻辑，只优化返回格式，避免AI困惑
"""

async def view_prompt(chat_id: str) -> dict:
    """
    查看专属提示词
    
    注意：实际查询逻辑由task_manager中的context_manager处理
    这里只是接口定义，返回格式优化的结果
    """
    return {
        "success": True,
        "chat_id": chat_id,
        "action": "view_prompt",
        "status": "awaiting_context_query",  # 等待上下文查询
        "note": "这是一个查询操作，请等待上下文管理器返回实际数据"
    }


async def set_prompt(chat_id: str, prompt_content: str) -> dict:
    """
    设置专属提示词
    
    注意：实际设置逻辑由task_manager中的context_manager处理
    这里返回格式优化的结果，确保AI明确知道任务已完成
    """
    return {
        "success": True,
        "chat_id": chat_id,
        "prompt_content": prompt_content,
        "action": "set_prompt",
        "status": "configuration_requested",  # 配置已请求
        "user_feedback": f"已收到设置提示词的请求：{prompt_content[:50]}...",
        "ai_instruction": "✅ 提示词设置请求已提交。请继续对话，无需进一步工具调用。",
        "next_step": "context_manager_will_handle"  # 上下文管理器将处理
    }


async def delete_prompt(chat_id: str) -> dict:
    """
    删除专属提示词
    
    注意：实际删除逻辑由task_manager中的context_manager处理
    """
    return {
        "success": True,
        "chat_id": chat_id,
        "action": "delete_prompt",
        "status": "deletion_requested",  # 删除已请求
        "user_feedback": "已收到删除提示词的请求",
        "ai_instruction": "✅ 提示词删除请求已提交。请继续对话，无需进一步工具调用。"
    }
'''
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, 
                lambda: self._write_file_sync(prompt_service_path, prompt_service_content)
            )
        
    def _write_file_sync(self, file_path: Path, content: str):
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
    def set_context_manager(self, context_manager):
        self.context_manager = context_manager
        
    async def _ensure_directories(self):
        if not self.tools_service_dir.exists():
            self.tools_service_dir.mkdir(parents=True, exist_ok=True)
                
    async def _scan_and_register_tools(self):
        async with self.lock:
            self.tools_registry.clear()
            self.tool_functions.clear()
            self.tool_definitions_cache.clear()
            
            tool_files = list(self.tools_service_dir.glob("*.py"))
            
            for tool_file in tool_files:
                try:
                    await self._register_tool_file(tool_file)
                except Exception as e:
                    self.logger.error(f"注册工具文件失败 {tool_file.name}: {e}")
            
            self._generate_tool_definitions_cache()
            
    async def _register_tool_file(self, tool_file: Path):
        module_name = tool_file.stem
        
        import sys
        import importlib.util
        
        sys.path.insert(0, str(tool_file.parent))
        
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(tool_file))
            if spec is None:
                return
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
                
        except Exception as e:
            self.logger.error(f"导入模块失败 {module_name}: {e}")
            return
            
        finally:
            if str(tool_file.parent) in sys.path:
                sys.path.remove(str(tool_file.parent))
                
        for name, obj in inspect.getmembers(module):
            if inspect.isfunction(obj) and not name.startswith('_'):
                is_async = asyncio.iscoroutinefunction(obj)
                await self._register_tool_function(module_name, name, obj, is_async)
                
    async def _register_tool_function(self, module_name: str, function_name: str, 
                                     function_obj: Callable, is_async: bool):
        tool_name = f"{module_name}_{function_name}"
        sig = inspect.signature(function_obj)
        docstring = inspect.getdoc(function_obj) or ""
        
        parameters = {}
        required_params = []
        
        for param_name, param in sig.parameters.items():
            if param_name == 'self':
                continue
                
            param_info = {
                "type": self._python_type_to_json_type(param.annotation),
                "description": ""
            }
            
            if param.default == inspect.Parameter.empty:
                required_params.append(param_name)
                
            parameters[param_name] = param_info
            
        description = self._parse_docstring_description(docstring)
        param_descriptions = self._parse_docstring_params(docstring)
        
        for param_name, param_desc in param_descriptions.items():
            if param_name in parameters:
                parameters[param_name]["description"] = param_desc
                
        tool_definition = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": parameters,
                    "required": required_params
                }
            }
        }
        
        self.tools_registry[tool_name] = tool_definition
        self.tool_functions[tool_name] = function_obj
        
    def _python_type_to_json_type(self, type_annotation) -> str:
        if type_annotation == str:
            return "string"
        elif type_annotation == int:
            return "integer"
        elif type_annotation == float:
            return "number"
        elif type_annotation == bool:
            return "boolean"
        elif type_annotation == dict:
            return "object"
        elif type_annotation == list:
            return "array"
        else:
            return "string"
            
    def _parse_docstring_description(self, docstring: str) -> str:
        if not docstring:
            return ""
            
        lines = docstring.strip().split('\n')
        description_lines = []
        
        for line in lines:
            line = line.strip()
            if line.startswith(':') or line.startswith('Args:'):
                break
            if line:
                description_lines.append(line)
                
        return ' '.join(description_lines)
        
    def _parse_docstring_params(self, docstring: str) -> Dict[str, str]:
        param_descriptions = {}
        
        if not docstring:
            return param_descriptions
            
        args_match = re.search(r'Args:(.*?)(?=\n\s*\n|\Z)', docstring, re.DOTALL)
        if not args_match:
            return param_descriptions
            
        args_text = args_match.group(1)
        param_pattern = re.compile(r'(\w+):\s*(.*?)(?=\n\s*\w+:|$)')
        matches = param_pattern.findall(args_text, re.DOTALL)
        
        for param_name, param_desc in matches:
            param_descriptions[param_name.strip()] = param_desc.strip()
            
        return param_descriptions
            
    def _generate_tool_definitions_cache(self):
        self.tool_definitions_cache = list(self.tools_registry.values())
        
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return self.tool_definitions_cache.copy()
        
    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any], 
                          session_id: str = None, chat_id: str = None) -> Dict[str, Any]:
        if tool_name not in self.tool_functions:
            return {"success": False, "error": f"工具不存在: {tool_name}"}
            
        try:
            function_obj = self.tool_functions[tool_name]
            is_async = asyncio.iscoroutinefunction(function_obj)
            
            sig = inspect.signature(function_obj)
            params = sig.parameters
            
            if 'session_id' in params and session_id:
                arguments['session_id'] = session_id
            if 'chat_id' in params and chat_id:
                arguments['chat_id'] = chat_id
                
            if is_async:
                result = await function_obj(**arguments)
            else:
                result = function_obj(**arguments)
            
            if not isinstance(result, dict):
                result = {"result": result}
                
            if "success" not in result:
                result["success"] = True
                
            return result
            
        except Exception as e:
            self.logger.error(f"执行工具失败 {tool_name}: {e}", exc_info=True)
            return {"success": False, "error": str(e), "tool_name": tool_name}
            
    async def reload_tools(self) -> Dict[str, Any]:
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
        if tool_name not in self.tools_registry:
            return None
            
        is_async = asyncio.iscoroutinefunction(self.tool_functions[tool_name]) if tool_name in self.tool_functions else False
        
        return {
            "name": tool_name,
            "definition": self.tools_registry[tool_name],
            "has_function": tool_name in self.tool_functions,
            "is_async": is_async
        }
        
    def list_tools(self) -> List[str]:
        return list(self.tools_registry.keys())