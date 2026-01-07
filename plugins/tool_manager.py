#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tool_manager.py - 完全异步工具函数管理器
基于现有架构的异步化改造
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
    """完全异步工具管理器"""
    
    def __init__(self, tools_service_dir: Path):
        self.logger = logging.getLogger(__name__)
        
        # 目录配置
        self.tools_service_dir = Path(tools_service_dir)
        
        # 工具注册表
        self.tools_registry = {}  # 工具名称 -> 工具定义
        self.tool_functions = {}  # 工具名称 -> 函数对象
        
        # 工具定义缓存
        self.tool_definitions_cache = []
        
        # 配置
        self.config = None
        
        # 异步锁
        self.lock = asyncio.Lock()
        
        # 上下文管理器引用
        self.context_manager = None
        
    async def initialize(self, config: Dict[str, Any]):
        """异步初始化工具管理器"""
        self.config = config
        
        # 确保目录存在
        await self._ensure_directories()
        
        # 确保prompt_service存在
        await self._ensure_prompt_service_exists()
        
        # 扫描并注册工具
        await self._scan_and_register_tools()
        
        self.logger.info(f"异步工具管理器初始化完成，已注册 {len(self.tools_registry)} 个工具")
        
    async def _ensure_prompt_service_exists(self):
        """确保prompt_service.py存在，如果不存在则自动创建"""
        prompt_service_path = self.tools_service_dir / "prompt_service.py"
        
        if not prompt_service_path.exists():
            self.logger.info("未找到prompt_service.py，正在自动创建...")
            
            prompt_service_content = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompt_service - 提示词管理工具
管理对话的专属提示词
"""

async def view_prompt(chat_id: str) -> dict:
    """
    查看对话的专属提示词
    
    Args:
        chat_id: 对话ID
        
    Returns:
        包含提示词信息的字典
    """
    return {
        "success": True,
        "chat_id": chat_id,
        "action": "view_prompt"
    }


async def set_prompt(chat_id: str, prompt_content: str) -> dict:
    """
    设置对话的专属提示词
    
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
        "action": "set_prompt"
    }


async def delete_prompt(chat_id: str) -> dict:
    """
    删除对话的专属提示词
    
    Args:
        chat_id: 对话ID
        
    Returns:
        删除结果
    """
    return {
        "success": True,
        "chat_id": chat_id,
        "action": "delete_prompt"
    }
'''
            
            # 异步写入文件
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, 
                lambda: self._write_file_sync(prompt_service_path, prompt_service_content)
            )
            self.logger.info(f"已创建prompt_service.py 在 {prompt_service_path}")
        
    def _write_file_sync(self, file_path: Path, content: str):
        """同步写入文件"""
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
    def set_context_manager(self, context_manager):
        """设置上下文管理器引用"""
        self.context_manager = context_manager
        self.logger.info("上下文管理器已注入工具管理器")
        
    async def _ensure_directories(self):
        """确保目录存在"""
        for directory in [self.tools_service_dir]:
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                
    async def _scan_and_register_tools(self):
        """异步扫描并注册所有工具"""
        async with self.lock:
            # 清空注册表
            self.tools_registry.clear()
            self.tool_functions.clear()
            self.tool_definitions_cache.clear()
            
            # 扫描tools_service目录
            tool_files = list(self.tools_service_dir.glob("*.py"))
            
            for tool_file in tool_files:
                try:
                    await self._register_tool_file(tool_file)
                except Exception as e:
                    self.logger.error(f"注册工具文件失败 {tool_file.name}: {e}")
            
            # 生成工具定义缓存
            self._generate_tool_definitions_cache()
            
    async def _register_tool_file(self, tool_file: Path):
        """异步注册工具文件"""
        # 提取模块名
        module_name = tool_file.stem
        
        # Python 3.10+兼容的导入方式
        import sys
        import importlib.util
        
        # 将文件路径添加到sys.path以便导入
        sys.path.insert(0, str(tool_file.parent))
        
        try:
            # 使用importlib.util导入模块
            spec = importlib.util.spec_from_file_location(module_name, str(tool_file))
            if spec is None:
                self.logger.error(f"无法加载模块: {module_name}")
                return
                
            module = importlib.util.module_from_spec(spec)
            
            try:
                spec.loader.exec_module(module)
            except Exception as e:
                self.logger.error(f"执行模块失败 {module_name}: {e}")
                return
                
        except Exception as e:
            self.logger.error(f"导入模块失败 {module_name}: {e}")
            return
            
        finally:
            # 从sys.path中移除添加的路径
            if str(tool_file.parent) in sys.path:
                sys.path.remove(str(tool_file.parent))
                
        # 查找模块中的工具函数
        for name, obj in inspect.getmembers(module):
            if inspect.isfunction(obj) and not name.startswith('_'):
                # 检查是否是异步函数
                is_async = asyncio.iscoroutinefunction(obj)
                if not is_async:
                    self.logger.warning(f"工具函数 {name} 不是异步的，建议改为异步函数")
                
                # 注册函数
                await self._register_tool_function(module_name, name, obj, is_async)
                
    async def _register_tool_function(self, module_name: str, function_name: str, 
                                     function_obj: Callable, is_async: bool):
        """异步注册工具函数"""
        tool_name = f"{module_name}_{function_name}"
        
        # 获取函数签名和文档字符串
        sig = inspect.signature(function_obj)
        docstring = inspect.getdoc(function_obj) or ""
        
        # 解析参数
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
            
        # 解析文档字符串
        description = self._parse_docstring_description(docstring)
        param_descriptions = self._parse_docstring_params(docstring)
        
        # 更新参数描述
        for param_name, param_desc in param_descriptions.items():
            if param_name in parameters:
                parameters[param_name]["description"] = param_desc
                
        # 构建工具定义
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
        
        # 注册工具
        self.tools_registry[tool_name] = tool_definition
        self.tool_functions[tool_name] = function_obj
        
        self.logger.debug(f"注册工具: {tool_name}, 异步: {is_async}")
        
    def _python_type_to_json_type(self, type_annotation) -> str:
        """Python类型转换为JSON类型"""
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
        """解析文档字符串的描述部分"""
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
        """解析文档字符串的参数描述"""
        param_descriptions = {}
        
        if not docstring:
            return param_descriptions
            
        # 查找Args部分
        args_match = re.search(r'Args:(.*?)(?=\n\s*\n|\Z)', docstring, re.DOTALL)
        if not args_match:
            return param_descriptions
            
        args_text = args_match.group(1)
        
        # 解析每个参数
        param_pattern = re.compile(r'(\w+):\s*(.*?)(?=\n\s*\w+:|$)')
        matches = param_pattern.findall(args_text, re.DOTALL)
        
        for param_name, param_desc in matches:
            param_descriptions[param_name.strip()] = param_desc.strip()
            
        return param_descriptions
            
    def _generate_tool_definitions_cache(self):
        """生成工具定义缓存"""
        self.tool_definitions_cache = list(self.tools_registry.values())
        
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """获取工具定义列表（OpenAI格式）"""
        return self.tool_definitions_cache.copy()
        
    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any], 
                          session_id: str = None, chat_id: str = None) -> Dict[str, Any]:
        """
        异步执行工具
        
        Args:
            tool_name: 工具名称
            arguments: 参数字典
            session_id: 会话ID（可选）
            chat_id: 对话ID（可选）
            
        Returns:
            执行结果
        """
        if tool_name not in self.tool_functions:
            return {
                "success": False,
                "error": f"工具不存在: {tool_name}"
            }
            
        try:
            function_obj = self.tool_functions[tool_name]
            is_async = asyncio.iscoroutinefunction(function_obj)
            
            # 添加session_id和chat_id到参数中
            sig = inspect.signature(function_obj)
            params = sig.parameters
            
            if 'session_id' in params and session_id:
                arguments['session_id'] = session_id
            if 'chat_id' in params and chat_id:
                arguments['chat_id'] = chat_id
                
            # 执行函数
            if is_async:
                # 异步函数使用await
                result = await function_obj(**arguments)
            else:
                # 同步函数直接调用（不建议）
                result = function_obj(**arguments)
            
            # 确保结果是字典
            if not isinstance(result, dict):
                result = {"result": result}
                
            # 添加成功标志
            if "success" not in result:
                result["success"] = True
                
            return result
            
        except Exception as e:
            self.logger.error(f"执行工具失败 {tool_name}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "tool_name": tool_name
            }
            
    async def reload_tools(self) -> Dict[str, Any]:
        """
        重新加载工具
        
        Returns:
            重载结果
        """
        try:
            # 重新扫描并注册工具
            await self._scan_and_register_tools()
            
            self.logger.info("工具系统已重载")
            
            return {
                "success": True,
                "message": f"工具系统已重载，当前注册 {len(self.tools_registry)} 个工具",
                "tool_count": len(self.tools_registry)
            }
            
        except Exception as e:
            self.logger.error(f"重载工具失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """获取工具信息"""
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
        """列出所有工具名称"""
        return list(self.tools_registry.keys())
        
    async def shutdown(self):
        """关闭工具管理器"""
        self.logger.info("异步工具管理器已关闭")