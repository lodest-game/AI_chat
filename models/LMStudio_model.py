#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LMStudio_model.py - 完全异步的LM Studio模型服务
简化版本，移除所有无效参数
"""

import asyncio
import aiohttp
import json
import logging
import time
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
        
        # 测试连接
        if await self._test_connection_async():
            self.is_connected = True
            self.is_running = True
            
            self.logger.info(f"异步LM Studio模型服务已启动: {self.base_url}")
        else:
            self.logger.error(f"异步LM Studio模型服务连接失败: {self.base_url}")
            
    def _get_config_file_path(self) -> Path:
        """获取配置文件路径"""
        module_dir = Path(__file__).parent
        return module_dir / "lmstudio_config.json"
        
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
                
                # 发送异步请求
                response = await self._call_openai_api_async(session_data)
                
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
                
    async def _call_openai_api_async(self, session_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """异步调用OpenAI API，直接使用session_data作为请求体"""
        try:
            endpoint = f"{self.base_url}/v1/chat/completions"
            headers = self._get_headers()
            
            # 记录请求开始时间
            start_time = time.time()
            
            # 记录请求数据（脱敏）
            log_data = session_data.copy()
            if "messages" in log_data:
                for i, msg in enumerate(log_data.get("messages", [])):
                    if "content" in msg and isinstance(msg["content"], str) and len(msg["content"]) > 50:
                        log_data["messages"][i]["content"] = msg["content"][:50] + "..."
            
            self.logger.debug(f"模型请求数据: {json.dumps(log_data, ensure_ascii=False)[:200]}...")
            
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
                        
                        self.logger.debug(f"模型请求成功: {request_time:.2f}秒")
                        
                        # 添加请求时间信息
                        result["_request_time"] = request_time
                        
                        return result
                    else:
                        error_text = await response.text()
                        self.logger.error(f"模型请求失败，状态码: {response.status}, 错误: {error_text[:200]}")
                        return None
                        
        except asyncio.TimeoutError:
            self.logger.error(f"模型请求超时")
            return None
        except aiohttp.ClientError as e:
            self.logger.error(f"HTTP客户端错误: {type(e).__name__}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"调用OpenAI API失败: {type(e).__name__}: {e}")
            return None
            
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
            "config_file": str(self.config_file) if self.config_file else None
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