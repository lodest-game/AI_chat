#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LMStudio_model.py - 完全异步的LM Studio模型服务
添加完整的请求体记录功能
"""

import asyncio
import aiohttp
import json
import logging
import time
import os
from pathlib import Path
from typing import Dict, Any, Optional


class Model:
    """完全异步的LM Studio模型服务"""
    
    def __init__(self):
        """初始化异步模型服务"""
        self.logger = logging.getLogger(__name__)
        
        # 配置
        self.config = {}
        self.config_file = None
        
        # 连接状态
        self.is_connected = False
        
        # 并发控制
        self.concurrent_requests = 0
        self.max_concurrent_requests = 10  # 默认并发数
        self.semaphore = None  # 异步信号量
        
        # 运行标志
        self.is_running = False
        
        # 连接参数
        self.base_url = "http://localhost:1234"
        self.api_key = ""
        
        # 请求统计
        self.request_counter = 0
        self.successful_requests = 0
        self.failed_requests = 0
        
        # 请求记录配置
        self.enable_request_logging = True  # 是否启用请求记录
        self.request_log_dir = None  # 请求记录目录
        self.max_request_log_size = 10485760  # 最大请求日志大小，10MB
        
    async def start(self, config: Dict[str, Any]):
        """
        异步启动模型服务
        
        Args:
            config: 服务配置
        """
        self.config = config
        
        # 获取配置参数
        connection_config = config.get("connection", {})
        self.base_url = connection_config.get("base_url", "http://localhost:1234")
        self.api_key = connection_config.get("api_key", "")
        
        # 生成配置文件路径
        self.config_file = self._get_config_file_path()
        
        # 加载或创建配置
        await self._load_or_create_config_async()
        
        # 应用配置
        self._apply_config()
        
        # 创建异步信号量控制并发
        performance_config = config.get("performance", {})
        self.max_concurrent_requests = performance_config.get("max_concurrent_requests", 10)
        self.semaphore = asyncio.Semaphore(self.max_concurrent_requests)
        
        # 初始化请求记录目录
        await self._init_request_log_dir()
        
        # 测试连接
        if await self._test_connection_async():
            self.is_connected = True
            self.is_running = True
            
            self.logger.info(f"异步LM Studio模型服务已启动: {self.base_url}")
            self.logger.info(f"请求记录目录: {self.request_log_dir}")
            self.logger.info(f"请求记录状态: {'启用' if self.enable_request_logging else '禁用'}")
        else:
            self.logger.error(f"异步LM Studio模型服务连接失败: {self.base_url}")
    
    def _get_config_file_path(self) -> Path:
        """获取配置文件路径"""
        module_dir = Path(__file__).parent
        return module_dir / "lmstudio_config.json"
    
    async def _init_request_log_dir(self):
        """初始化请求记录目录"""
        try:
            # 创建请求记录目录
            module_dir = Path(__file__).parent
            self.request_log_dir = module_dir / "request_logs"
            self.request_log_dir.mkdir(exist_ok=True)
            
            # 清理旧的日志文件（如果超过最大限制）
            await self._cleanup_old_request_logs()
            
        except Exception as e:
            self.logger.error(f"初始化请求记录目录失败: {e}")
            self.request_log_dir = None
    
    async def _cleanup_old_request_logs(self):
        """清理旧的请求日志文件"""
        if not self.request_log_dir or not self.request_log_dir.exists():
            return
        
        try:
            # 获取所有请求日志文件
            log_files = list(self.request_log_dir.glob("request_*.json"))
            
            if not log_files:
                return
            
            # 按修改时间排序
            log_files.sort(key=lambda x: x.stat().st_mtime)
            
            # 计算总大小
            total_size = sum(f.stat().st_size for f in log_files)
            
            # 如果超过最大限制，删除最旧的文件
            while total_size > self.max_request_log_size and log_files:
                oldest_file = log_files.pop(0)
                file_size = oldest_file.stat().st_size
                oldest_file.unlink()
                total_size -= file_size
                self.logger.debug(f"删除旧的请求日志文件: {oldest_file.name}")
                
        except Exception as e:
            self.logger.error(f"清理旧的请求日志文件失败: {e}")
    
    async def _load_or_create_config_async(self):
        """异步加载或创建配置文件"""
        if self.config_file.exists():
            try:
                loop = asyncio.get_event_loop()
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = await loop.run_in_executor(None, json.load, f)
                
                # 合并配置（文件配置覆盖传入配置）
                self.config = {**self.config, **loaded_config}
                self.logger.info(f"从文件加载配置: {self.config_file}")
                
            except Exception as e:
                self.logger.error(f"加载配置文件失败: {e}, 创建默认配置")
                await self._create_default_config_async()
        else:
            await self._create_default_config_async()
    
    async def _create_default_config_async(self):
        """异步创建默认配置并保存到文件"""
        default_config = {
            "connection": {
                "base_url": "http://localhost:1234",
                "api_key": ""
            },
            "performance": {
                "max_concurrent_requests": 10
            },
            "logging": {
                "enable_request_logging": True,
                "max_request_log_size": 10485760
            }
        }
        
        # 更新到当前配置
        self.config = {**self.config, **default_config}
        
        # 异步保存到文件
        try:
            loop = asyncio.get_event_loop()
            with open(self.config_file, 'w', encoding='utf-8') as f:
                await loop.run_in_executor(
                    None,
                    lambda: json.dump(default_config, f, ensure_ascii=False, indent=2)
                )
            self.logger.info(f"默认配置文件已创建: {self.config_file}")
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
    
    def _apply_config(self):
        """应用配置"""
        connection_config = self.config.get("connection", {})
        self.base_url = connection_config.get("base_url", self.base_url)
        self.api_key = connection_config.get("api_key", self.api_key)
        
        performance_config = self.config.get("performance", {})
        self.max_concurrent_requests = performance_config.get("max_concurrent_requests", self.max_concurrent_requests)
        
        # 应用日志配置
        logging_config = self.config.get("logging", {})
        self.enable_request_logging = logging_config.get("enable_request_logging", True)
        self.max_request_log_size = logging_config.get("max_request_log_size", 10485760)
    
    async def _save_request_to_file(self, request_data: Dict[str, Any], request_id: int):
        """将完整请求体保存到文件 - 不进行任何处理"""
        if not self.enable_request_logging or not self.request_log_dir:
            return
        
        try:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"request_{timestamp}_{request_id:06d}.json"
            filepath = self.request_log_dir / filename
            
            # 保存完整的原始数据
            loop = asyncio.get_event_loop()
            
            request_metadata = {
                "timestamp": time.time(),
                "request_id": request_id,
                "request_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data_size": len(json.dumps(request_data, ensure_ascii=False)),
                "data": request_data  # 完整原始数据
            }
            
            await loop.run_in_executor(
                None,
                lambda: self._save_json_sync(filepath, request_metadata)
            )
            
            self.logger.info(f"请求 #{request_id}: 完整请求体已保存到文件: {filename}")
            
        except Exception as e:
            self.logger.error(f"保存请求体到文件失败: {e}")
    
    def _save_json_sync(self, filepath: Path, data: Dict[str, Any]):
        """同步保存JSON数据到文件"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    async def _test_connection_async(self) -> bool:
        """异步测试连接"""
        try:
            endpoint = f"{self.base_url}/v1/models"
            
            headers = self._get_headers()
            
            # 使用独立的会话进行测试
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(endpoint, headers=headers) as response:
                    if response.status == 200:
                        self.logger.info(f"LM Studio连接成功: {self.base_url}")
                        return True
                    else:
                        self.logger.error(f"LM Studio连接失败，状态码: {response.status}")
                        return False
                        
        except Exception as e:
            self.logger.error(f"LM Studio连接测试失败: {e}")
            return False
    
    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {
            "Content-Type": "application/json"
        }
        
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            
        return headers
    
    async def send_request_async(self, request_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        异步发送请求到模型服务
        
        Args:
            request_data: 请求数据
            
        Returns:
            模型响应
        """
        # 使用信号量控制并发
        async with self.semaphore:
            try:
                self.request_counter += 1
                current_request = self.request_counter
                
                # 直接使用session_data作为请求体
                session_data = request_data.get("session_data", {})
                
                # 记录请求开始
                self.logger.debug(f"开始处理模型请求 #{current_request}")
                start_time = time.time()
                
                # 保存完整请求体到文件
                await self._save_request_to_file(session_data, current_request)
                
                # 分析请求数据
                await self._analyze_request_data(session_data, current_request)
                
                # 发送异步请求
                response = await self._call_openai_api_async(session_data, current_request)
                
                # 记录请求结束
                request_time = time.time() - start_time
                
                if response:
                    self.successful_requests += 1
                    self.logger.info(f"模型请求 #{current_request} 成功: {request_time:.2f}秒")
                    response["_request_time"] = request_time
                    response["_request_id"] = current_request
                else:
                    self.failed_requests += 1
                    self.logger.warning(f"模型请求 #{current_request} 失败: {request_time:.2f}秒")
                    
                return response
                
            except Exception as e:
                self.logger.error(f"发送模型请求失败: {e}")
                return None
    
    async def _analyze_request_data(self, session_data: Dict[str, Any], request_id: int):
        """
        分析请求数据 - 只记录原始信息，不进行任何处理
        """
        try:
            if "messages" not in session_data:
                self.logger.info(f"请求 #{request_id}: 缺少'messages'字段")
                return
            
            messages = session_data.get("messages", [])
            self.logger.info(f"请求 #{request_id}: 原始消息数量: {len(messages)}")
            
            # 只记录原始信息，不进行分析或格式化
            for i, message in enumerate(messages):
                role = message.get("role", "unknown")
                content = message.get("content", "")
                
                self.logger.info(f"请求 #{request_id}: 消息{i} 原始角色: {role}")
                self.logger.info(f"请求 #{request_id}: 消息{i} 原始内容类型: {type(content).__name__}")
                self.logger.info(f"请求 #{request_id}: 消息{i} 原始内容: {repr(content)}")
            
            # 记录请求总大小
            request_size = len(json.dumps(session_data, ensure_ascii=False))
            self.logger.info(f"请求 #{request_id}: 请求体大小: {request_size} 字节")
            
        except Exception as e:
            self.logger.info(f"请求 #{request_id}: 分析请求数据失败: {e}")
    
    def _estimate_tokens(self, session_data: Dict[str, Any]) -> int:
        """
        粗略估计请求的token数量
        
        Args:
            session_data: 请求数据
            
        Returns:
            估计的token数
        """
        try:
            total_tokens = 0
            
            # 估计模型参数的token
            if "model" in session_data:
                total_tokens += len(session_data["model"]) // 4
            
            # 估计消息的token
            if "messages" in session_data:
                for message in session_data["messages"]:
                    # 角色
                    if "role" in message:
                        total_tokens += len(message["role"]) // 4
                    
                    # 内容
                    if "content" in message:
                        content = message["content"]
                        if isinstance(content, str):
                            total_tokens += len(content) // 4
                        elif isinstance(content, list):
                            for item in content:
                                if isinstance(item, dict) and "text" in item:
                                    total_tokens += len(item["text"]) // 4
            
            return total_tokens
        except Exception as e:
            self.logger.error(f"估计token数失败: {e}")
            return 0
    
    async def _call_openai_api_async(self, session_data: Dict[str, Any], request_id: int) -> Optional[Dict[str, Any]]:
        """异步调用OpenAI API，直接使用session_data作为请求体"""
        try:
            endpoint = f"{self.base_url}/v1/chat/completions"
            headers = self._get_headers()
            
            # 记录请求开始时间
            start_time = time.time()
            
            # 记录请求数据（脱敏）- 用于日志
            log_data = session_data.copy()
            if "messages" in log_data:
                for i, msg in enumerate(log_data.get("messages", [])):
                    if "content" in msg and isinstance(msg["content"], str):
                        # 保存原始长度信息
                        original_length = len(msg["content"])
            
            self.logger.debug(f"请求 #{request_id}: 发送到模型的数据预览: {json.dumps(log_data, ensure_ascii=False)[:500]}...")
            
            # 记录完整的请求信息
            self._log_complete_request_info(session_data, request_id)
            
            # 为每个请求创建独立的会话，不设置超时
            async with aiohttp.ClientSession() as session:
                # 发送异步HTTP请求
                async with session.post(
                    endpoint, 
                    json=session_data,  # 直接使用session_data
                    headers=headers
                ) as response:
                    
                    response_status = response.status
                    
                    if response_status == 200:
                        result = await response.json()
                        
                        # 计算请求耗时
                        request_time = time.time() - start_time
                        
                        self.logger.debug(f"请求 #{request_id}: 模型请求成功: {request_time:.2f}秒")
                        
                        # 添加请求时间信息
                        result["_request_time"] = request_time
                        
                        return result
                    else:
                        error_text = await response.text()
                        self.logger.error(f"请求 #{request_id}: 模型请求失败，状态码: {response.status}, 错误: {error_text[:200]}")
                        return None
                        
        except asyncio.TimeoutError:
            self.logger.error(f"请求 #{request_id}: 模型请求超时")
            return None
        except aiohttp.ClientError as e:
            self.logger.error(f"请求 #{request_id}: HTTP客户端错误: {type(e).__name__}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"请求 #{request_id}: 调用OpenAI API失败: {type(e).__name__}: {e}")
            return None
    
    def _log_complete_request_info(self, session_data: Dict[str, Any], request_id: int):
        """
        记录完整的请求信息到日志 - 不截断任何内容
        """
        try:
            messages = session_data.get("messages", [])
            
            self.logger.info(f"=== 请求 #{request_id} 完整信息开始 ===")
            
            for i, message in enumerate(messages):
                role = message.get("role", "unknown")
                content = message.get("content", "")
                
                self.logger.info(f"消息 {i} - 角色: {role}")
                
                # 记录消息类型
                if isinstance(content, str):
                    self.logger.info(f"消息 {i} 内容类型: 字符串")
                    # 不截断，记录完整内容
                    content_lines = content.split('\n')
                    for line_num, line in enumerate(content_lines):
                        self.logger.info(f"消息 {i} 第{line_num+1}行: {line}")
                elif isinstance(content, list):
                    self.logger.info(f"消息 {i} 内容类型: 列表，长度: {len(content)}")
                    for item_idx, item in enumerate(content):
                        if isinstance(item, dict):
                            item_type = item.get("type", "unknown")
                            if item_type == "text":
                                text = item.get("text", "")
                                text_lines = text.split('\n')
                                for line_num, line in enumerate(text_lines):
                                    self.logger.info(f"消息 {i} 项目{item_idx}(文本) 第{line_num+1}行: {line}")
                            elif item_type == "image_url":
                                image_url = item.get("image_url", {})
                                if isinstance(image_url, dict):
                                    url = image_url.get("url", "")
                                    self.logger.info(f"消息 {i} 项目{item_idx}(图片) URL: {url}")
                else:
                    self.logger.info(f"消息 {i} 内容: {content}")
            
            # 记录总消息数量
            self.logger.info(f"请求 #{request_id}: 总消息数量: {len(messages)}")
            
            self.logger.info(f"=== 请求 #{request_id} 完整信息结束 ===")
            
        except Exception as e:
            self.logger.error(f"记录完整请求信息失败: {e}")
    
    async def is_connected_async(self) -> bool:
        """异步检查连接状态"""
        if not self.is_connected:
            return False
            
        # 异步检查连接
        try:
            endpoint = f"{self.base_url}/v1/models"
            headers = self._get_headers()
            
            # 使用短超时进行连接检查
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(endpoint, headers=headers) as response:
                    return response.status == 200
                    
        except Exception:
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """异步获取服务状态"""
        status = {
            "is_connected": self.is_connected,
            "is_running": self.is_running,
            "base_url": self.base_url,
            "concurrent_requests": self.max_concurrent_requests - (self.semaphore._value if self.semaphore else 0),
            "max_concurrent_requests": self.max_concurrent_requests,
            "request_counter": self.request_counter,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "config_file": str(self.config_file) if self.config_file else None,
            "request_logging_enabled": self.enable_request_logging,
            "request_log_dir": str(self.request_log_dir) if self.request_log_dir else None
        }
            
        return status
    
    async def update_config(self, key_path: str, value: Any) -> bool:
        """
        异步更新配置
        
        Args:
            key_path: 配置键路径
            value: 配置值
            
        Returns:
            是否成功
        """
        try:
            # 解析键路径
            keys = key_path.split(".")
            config_ref = self.config
            
            # 遍历到最后一个键之前
            for key in keys[:-1]:
                if key not in config_ref:
                    config_ref[key] = {}
                config_ref = config_ref[key]
                
            # 设置值
            config_ref[keys[-1]] = value
            
            # 异步保存到文件
            if self.config_file:
                loop = asyncio.get_event_loop()
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    await loop.run_in_executor(
                        None,
                        lambda: json.dump(self.config, f, ensure_ascii=False, indent=2)
                    )
                    
            # 重新应用配置
            self._apply_config()
            
            self.logger.info(f"配置已更新: {key_path} = {value}")
            return True
            
        except Exception as e:
            self.logger.error(f"更新配置失败: {e}")
            return False
    
    async def stop(self):
        """异步停止模型服务"""
        self.is_running = False
        self.is_connected = False
        
        self.logger.info("异步LM Studio模型服务已停止")
        self.logger.info(f"总共处理了 {self.request_counter} 个请求")
        self.logger.info(f"成功: {self.successful_requests}, 失败: {self.failed_requests}")
        
        # 如果启用了请求记录，记录最终统计
        if self.enable_request_logging and self.request_log_dir:
            log_files = list(self.request_log_dir.glob("request_*.json"))
            if log_files:
                total_size = sum(f.stat().st_size for f in log_files)
                self.logger.info(f"请求记录: 共 {len(log_files)} 个文件, 总大小: {total_size} 字节")