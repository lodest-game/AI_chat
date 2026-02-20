# LMStudio_model.py
#!/usr/bin/env python3

import asyncio
import aiohttp
import json
import logging
import time
from pathlib import Path

class Model:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        self.config = {}
        self.config_file = None
        
        self.is_connected = False
        self.concurrent_requests = 0
        self.max_concurrent_requests = 10
        self.semaphore = None
        
        self.is_running = False
        
        self.base_url = "http://localhost:1234"
        self.api_key = ""
        
        self.request_counter = 0
        self.successful_requests = 0
        self.failed_requests = 0
        
    async def start(self, config):
        self.config = config
        
        connection_config = config.get("connection", {})
        self.base_url = connection_config.get("base_url", "http://localhost:1234")
        self.api_key = connection_config.get("api_key", "")
        
        self.config_file = self._get_config_file_path()
        
        await self._load_or_create_config_async()
        
        self._apply_config()
        
        performance_config = config.get("performance", {})
        self.max_concurrent_requests = performance_config.get("max_concurrent_requests", 10)
        self.semaphore = asyncio.Semaphore(self.max_concurrent_requests)
        
        if await self._test_connection_async():
            self.is_connected = True
            self.is_running = True
            
            self.logger.info(f"异步LM Studio模型服务已启动: {self.base_url}")
        else:
            self.logger.error(f"异步LM Studio模型服务连接失败: {self.base_url}")
            
    def _get_config_file_path(self):
        module_dir = Path(__file__).parent
        return module_dir / "lmstudio_config.json"
        
    async def _load_or_create_config_async(self):
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    
                self.config = {**self.config, **loaded_config}
                self.logger.info(f"从文件加载配置: {self.config_file}")
                
            except Exception as e:
                self.logger.error(f"加载配置文件失败: {e}, 创建默认配置")
                await self._create_default_config_async()
        else:
            await self._create_default_config_async()
            
    async def _create_default_config_async(self):
        default_config = {
            "connection": {
                "base_url": "http://localhost:1234",
                "api_key": ""
            },
            "performance": {
                "max_concurrent_requests": 10
            }
        }
        
        self.config = {**self.config, **default_config}
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            self.logger.info(f"默认配置文件已创建: {self.config_file}")
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
            
    def _apply_config(self):
        connection_config = self.config.get("connection", {})
        self.base_url = connection_config.get("base_url", self.base_url)
        self.api_key = connection_config.get("api_key", self.api_key)
        
        performance_config = self.config.get("performance", {})
        self.max_concurrent_requests = performance_config.get("max_concurrent_requests", self.max_concurrent_requests)
        
    async def _test_connection_async(self):
        try:
            endpoint = f"{self.base_url}/v1/models"
            
            headers = self._get_headers()
            
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
            
    def _get_headers(self):
        headers = {
            "Content-Type": "application/json"
        }
        
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            
        return headers
        
    async def send_request_async(self, request_data):
        async with self.semaphore:
            try:
                self.request_counter += 1
                current_request = self.request_counter
                
                session_data = request_data.get("session_data", {})
                
                self.logger.debug(f"开始处理模型请求 #{current_request}")
                start_time = time.time()
                
                response = await self._call_openai_api_async(session_data)
                
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
                
    async def _call_openai_api_async(self, session_data):
        try:
            endpoint = f"{self.base_url}/v1/chat/completions"
            headers = self._get_headers()
            
            start_time = time.time()
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint, 
                    json=session_data,
                    headers=headers
                ) as response:
                    
                    response_status = response.status
                    
                    if response_status == 200:
                        result = await response.json()
                        
                        request_time = time.time() - start_time
                        
                        self.logger.debug(f"模型请求成功: {request_time:.2f}秒")
                        
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
            
    async def is_connected_async(self):
        if not self.is_connected:
            return False
            
        try:
            endpoint = f"{self.base_url}/v1/models"
            headers = self._get_headers()
            
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(endpoint, headers=headers) as response:
                    return response.status == 200
                    
        except Exception:
            return False
            
    async def get_status(self):
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
        
    async def update_config(self, key_path, value):
        try:
            keys = key_path.split(".")
            config_ref = self.config
            
            for key in keys[:-1]:
                if key not in config_ref:
                    config_ref[key] = {}
                config_ref = config_ref[key]
                
            config_ref[keys[-1]] = value
            
            if self.config_file:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, ensure_ascii=False, indent=2)
                    
            self._apply_config()
            
            self.logger.info(f"配置已更新: {key_path} = {value}")
            return True
            
        except Exception as e:
            self.logger.error(f"更新配置失败: {e}")
            return False
            
    async def stop(self):
        self.is_running = False
        self.is_connected = False
        
        self.logger.info("异步LM Studio模型服务已停止")