#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIsearch_service.py - 百度AI搜索工具业务
支持联网搜索和API密钥管理
"""

import os
import json
import logging
import asyncio
import aiohttp
import time
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
import re

# ==================== 上下文管理器引用 ====================
_context_manager = None
_logger = logging.getLogger(__name__)

# ==================== 工具定义 ====================
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "aisearch_web_search",
            "description": "使用百度AI搜索进行联网搜索，获取最新信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "对话ID，用于权限检查"
                    },
                    "search_query": {
                        "type": "string",
                        "description": "要搜索的关键词或问题"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数（1-20，默认10）",
                        "minimum": 1,
                        "maximum": 20
                    }
                },
                "required": ["chat_id", "search_query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "aisearch_manage_key",
            "description": "为当前对话配置/修改百度AI搜索的私有API密钥",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "对话ID"
                    },
                    "private_api_key": {
                        "type": "string",
                        "description": "要设置的私有API密钥（留空则清除）"
                    }
                },
                "required": ["chat_id"]
            }
        }
    }
]

# ==================== 工具配置 ====================
TOOL_CONFIGS = {
    "aisearch_web_search": {
        "timeout": 60.0,
        "max_retries": 2,
        "enabled": True
    },
    "aisearch_manage_key": {
        "timeout": 10.0,
        "max_retries": 1,
        "enabled": True
    }
}

# ==================== 配置文件管理 ====================
@dataclass
class AIsearchConfig:
    """AI搜索配置类"""
    allow_all: bool = True  # 是否允许所有人使用
    default_api_key: str = ""  # 默认API密钥
    default_list: List[str] = None  # 默认名单（允许使用默认密钥的chat_id列表）
    private_list: Dict[str, str] = None  # 私有名单（chat_id -> private_api_key）
    
    def __post_init__(self):
        if self.default_list is None:
            self.default_list = []
        if self.private_list is None:
            self.private_list = {}

class AIsearchManager:
    """AI搜索管理器"""
    
    def __init__(self, config_dir: Path = None):
        self.config_dir = config_dir or Path(__file__).parent
        self.config_file = self.config_dir / "aisearch_config.json"
        self.config = AIsearchConfig()
        self.request_log_dir = self.config_dir / "request_logs"
        self.request_log_dir.mkdir(exist_ok=True)
        
    def _ensure_config_exists(self):
        """确保配置文件存在，不存在则创建默认配置"""
        if not self.config_file.exists():
            self.config = AIsearchConfig(
                allow_all=True,
                default_api_key="",
                default_list=[],
                private_list={}
            )
            self._save_config()
            _logger.info(f"创建默认配置文件: {self.config_file}")
            
    def _load_config(self):
        """加载配置文件"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            self.config = AIsearchConfig(
                allow_all=data.get("allow", True),
                default_api_key=data.get("default_API_key", ""),
                default_list=data.get("default", []),
                private_list=data.get("private", {})
            )
            return True
        except Exception as e:
            _logger.error(f"加载配置文件失败: {e}")
            self._ensure_config_exists()
            return False
            
    def _save_config(self):
        """保存配置文件"""
        try:
            config_data = {
                "allow": self.config.allow_all,
                "default_API_key": self.config.default_api_key,
                "default": self.config.default_list,
                "private": self.config.private_list
            }
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            _logger.error(f"保存配置文件失败: {e}")
            return False
            
    def get_api_key_for_chat(self, chat_id: str) -> Tuple[Optional[str], str]:
        """
        根据流程图逻辑获取API密钥
        返回: (api_key, status_message)
        """
        # 1. 检查private名单
        if chat_id in self.config.private_list:
            api_key = self.config.private_list[chat_id]
            if api_key:
                return api_key, f"使用私有API密钥（对话: {chat_id}）"
                
        # 2. 检查是否允许所有人使用
        if self.config.allow_all and self.config.default_api_key:
            return self.config.default_api_key, "使用默认API密钥（所有人允许）"
            
        # 3. 检查default名单
        if chat_id in self.config.default_list and self.config.default_api_key:
            return self.config.default_api_key, f"使用默认API密钥（对话在默认名单中: {chat_id}）"
            
        # 4. 拒绝
        return None, "权限拒绝：对话不在任何名单中且不允许所有人使用"
        
    def update_private_key(self, chat_id: str, private_api_key: str = None) -> Tuple[bool, str]:
        """更新私有API密钥"""
        if private_api_key and private_api_key.strip():
            # 设置或更新密钥
            self.config.private_list[chat_id] = private_api_key.strip()
            action = "设置" if chat_id not in self.config.private_list else "更新"
        else:
            # 清除密钥
            if chat_id in self.config.private_list:
                del self.config.private_list[chat_id]
                action = "清除"
            else:
                return False, "对话没有私有API密钥"
                
        if self._save_config():
            return True, f"已{action}对话 {chat_id} 的私有API密钥"
        else:
            return False, "保存配置失败"
            
    def get_chat_status(self, chat_id: str) -> Dict[str, Any]:
        """获取对话状态信息"""
        has_private_key = chat_id in self.config.private_list and bool(self.config.private_list[chat_id])
        in_default_list = chat_id in self.config.default_list
        
        return {
            "chat_id": chat_id,
            "has_private_key": has_private_key,
            "in_default_list": in_default_list,
            "allow_all": self.config.allow_all,
            "has_default_key": bool(self.config.default_api_key)
        }
        
    def log_request(self, chat_id: str, tool_name: str, request_data: Dict[str, Any], 
                    response_data: Dict[str, Any], success: bool, error_msg: str = ""):
        """记录请求日志"""
        try:
            timestamp = int(time.time())
            date_str = time.strftime("%Y-%m-%d", time.localtime())
            log_file = self.request_log_dir / f"requests_{date_str}.jsonl"
            
            log_entry = {
                "timestamp": timestamp,
                "datetime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "chat_id": chat_id,
                "tool_name": tool_name,
                "success": success,
                "request": request_data,
                "response": response_data,
                "error": error_msg
            }
            
            # 使用JSONL格式追加日志
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                
        except Exception as e:
            _logger.error(f"记录请求日志失败: {e}")

# ==================== 全局管理器实例 ====================
_aisearch_manager = None

def get_aisearch_manager() -> AIsearchManager:
    """获取AI搜索管理器实例（单例）"""
    global _aisearch_manager
    if _aisearch_manager is None:
        _aisearch_manager = AIsearchManager()
        _aisearch_manager._ensure_config_exists()
        _aisearch_manager._load_config()
    return _aisearch_manager

def set_context_manager(context_manager):
    """设置上下文管理器引用"""
    global _context_manager
    _context_manager = context_manager
    _logger.info("AIsearch服务已注入上下文管理器")

# ==================== 工具处理函数 ====================

async def aisearch_web_search(chat_id: str, search_query: str, max_results: int = 10) -> str:
    """
    百度AI搜索 - 联网搜索工具
    返回原始字符串内容，明确告诉AI任务已完成
    """
    manager = get_aisearch_manager()
    
    # 1. 获取API密钥（按照流程图逻辑）
    api_key, status_msg = manager.get_api_key_for_chat(chat_id)
    if not api_key:
        error_msg = f"搜索失败：{status_msg}"
        _logger.warning(f"搜索权限拒绝: chat_id={chat_id}, status={status_msg}")
        
        # 记录日志
        manager.log_request(
            chat_id=chat_id,
            tool_name="aisearch_web_search",
            request_data={"search_query": search_query, "max_results": max_results},
            response_data={},
            success=False,
            error_msg=error_msg
        )
        
        return f"【搜索任务失败】{error_msg}"
    
    _logger.info(f"开始AI搜索: chat_id={chat_id}, query={search_query[:50]}..., {status_msg}")
    
    # 2. 构建请求参数
    request_data = {
        "messages": [
            {
                "content": search_query,
                "role": "user"
            }
        ],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [
            {
                "type": "web",
                "top_k": min(max_results, 20)  # API限制最大20
            }
        ],
        "stream": False,
        "model": "ernie-4.5-turbo-32k",
        "enable_deep_search": False,
        "search_mode": "required",  # 必须执行搜索
        "enable_corner_markers": True,
        "max_completion_tokens": 2000
    }
    
    # 3. 发送请求到百度API
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    url = "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
    
    try:
        async with aiohttp.ClientSession() as session:
            start_time = time.time()
            
            async with session.post(
                url=url,
                headers=headers,
                json=request_data,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                response_time = time.time() - start_time
                
                if response.status == 200:
                    response_data = await response.json()
                    
                    # 提取搜索结果
                    if "choices" in response_data and len(response_data["choices"]) > 0:
                        content = response_data["choices"][0]["message"]["content"]
                        
                        # 如果有参考文献，添加引用信息
                        if "references" in response_data and response_data["references"]:
                            ref_count = len(response_data["references"])
                            content += f"\n\n【本次搜索共参考了{ref_count}个来源，信息更新时间：{time.strftime('%Y-%m-%d %H:%M:%S')}】"
                        
                        result = content
                        
                        # 记录成功日志
                        manager.log_request(
                            chat_id=chat_id,
                            tool_name="aisearch_web_search",
                            request_data={
                                "search_query": search_query,
                                "max_results": max_results,
                                "api_key_used": status_msg
                            },
                            response_data={
                                "response_time": response_time,
                                "has_content": True,
                                "reference_count": len(response_data.get("references", [])),
                                "raw_response_keys": list(response_data.keys())
                            },
                            success=True,
                            error_msg=""
                        )
                        
                        _logger.info(f"AI搜索成功: chat_id={chat_id}, 耗时={response_time:.2f}s")
                        return result
                    else:
                        error_msg = "API返回格式异常，无法提取搜索结果"
                else:
                    error_msg = f"HTTP错误: {response.status}"
                    try:
                        error_data = await response.json()
                        error_msg = f"{error_msg} - {error_data.get('message', '未知错误')}"
                    except:
                        pass
        
        # 记录失败日志
        manager.log_request(
            chat_id=chat_id,
            tool_name="aisearch_web_search",
            request_data={
                "search_query": search_query,
                "max_results": max_results,
                "api_key_used": status_msg
            },
            response_data={
                "response_time": response_time,
                "status_code": response.status if 'response' in locals() else None
            },
            success=False,
            error_msg=error_msg
        )
        
        return f"【搜索任务失败】{error_msg}"
        
    except asyncio.TimeoutError:
        error_msg = "请求超时（60秒）"
        _logger.error(f"AI搜索超时: chat_id={chat_id}")
        
        manager.log_request(
            chat_id=chat_id,
            tool_name="aisearch_web_search",
            request_data={"search_query": search_query, "max_results": max_results},
            response_data={},
            success=False,
            error_msg=error_msg
        )
        
        return f"【搜索任务失败】{error_msg}"
        
    except Exception as e:
        error_msg = f"请求异常: {str(e)}"
        _logger.error(f"AI搜索异常: chat_id={chat_id}, error={error_msg}")
        
        manager.log_request(
            chat_id=chat_id,
            tool_name="aisearch_web_search",
            request_data={"search_query": search_query, "max_results": max_results},
            response_data={},
            success=False,
            error_msg=error_msg
        )
        
        return f"【搜索任务失败】{error_msg}"

async def aisearch_manage_key(chat_id: str, private_api_key: str = None) -> str:
    """
    管理私有API密钥工具
    """
    manager = get_aisearch_manager()
    
    try:
        if private_api_key is None or not private_api_key.strip():
            # 清除密钥
            success, message = manager.update_private_key(chat_id, None)
        else:
            # 验证API密钥格式（基本格式检查）
            key_pattern = r'^[A-Za-z0-9\-_\.]+$'
            if not re.match(key_pattern, private_api_key.strip()):
                return "【密钥配置失败】API密钥格式无效，请检查是否正确"
                
            success, message = manager.update_private_key(chat_id, private_api_key)
        
        if success:
            # 记录日志
            action = "清除" if private_api_key is None or not private_api_key.strip() else "设置"
            manager.log_request(
                chat_id=chat_id,
                tool_name="aisearch_manage_key",
                request_data={"action": action, "has_key": bool(private_api_key and private_api_key.strip())},
                response_data={"success": True, "message": message},
                success=True,
                error_msg=""
            )
            
            # 获取当前状态
            status = manager.get_chat_status(chat_id)
            
            status_info = []
            if status["has_private_key"]:
                status_info.append("已配置私有密钥")
            if status["in_default_list"]:
                status_info.append("在默认名单中")
            if status["allow_all"]:
                status_info.append("所有人允许使用")
            if status["has_default_key"]:
                status_info.append("系统有默认密钥")
                
            status_str = "，".join(status_info) if status_info else "无特殊权限"
            
            return f"【密钥配置完成】{message}\n\n当前状态：{status_str}"
        else:
            manager.log_request(
                chat_id=chat_id,
                tool_name="aisearch_manage_key",
                request_data={"action": "update", "has_key": bool(private_api_key)},
                response_data={"success": False, "message": message},
                success=False,
                error_msg=message
            )
            
            return f"【密钥配置失败】{message}"
            
    except Exception as e:
        error_msg = f"配置异常: {str(e)}"
        _logger.error(f"密钥管理异常: chat_id={chat_id}, error={error_msg}")
        
        manager.log_request(
            chat_id=chat_id,
            tool_name="aisearch_manage_key",
            request_data={"action": "update", "has_key": bool(private_api_key)},
            response_data={},
            success=False,
            error_msg=error_msg
        )
        
        return f"【密钥配置失败】{error_msg}"

# ==================== 工具注册映射 ====================
TOOL_HANDLERS = {
    "aisearch_web_search": aisearch_web_search,
    "aisearch_manage_key": aisearch_manage_key
}

# ==================== 工具配置获取函数 ====================
async def get_aisearch_config_status(chat_id: str = None) -> Dict[str, Any]:
    """
    获取AI搜索配置状态（可用于调试）
    """
    manager = get_aisearch_manager()
    
    config_info = {
        "allow_all": manager.config.allow_all,
        "has_default_key": bool(manager.config.default_api_key),
        "default_list_count": len(manager.config.default_list),
        "private_list_count": len(manager.config.private_list),
        "config_file": str(manager.config_file),
        "log_dir": str(manager.request_log_dir)
    }
    
    if chat_id:
        config_info["chat_status"] = manager.get_chat_status(chat_id)
        
    return config_info

# ==================== 初始化函数 ====================
async def initialize_aisearch():
    """初始化AI搜索服务"""
    manager = get_aisearch_manager()
    _logger.info(f"AI搜索服务初始化完成，配置文件: {manager.config_file}")
    
    # 检查配置状态
    config_status = await get_aisearch_config_status()
    _logger.info(f"AI搜索配置状态: {json.dumps(config_status, ensure_ascii=False, indent=2)}")
    
    return True

# 模块加载时自动初始化
asyncio.create_task(initialize_aisearch())