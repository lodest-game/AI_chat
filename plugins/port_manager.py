#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
port_manager.py - 端口与路由管理器
移除线程池和所有同步兼容代码
"""

import os
import importlib
import logging
import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
import traceback


@dataclass
class ClientConnection:
    """客户端连接信息"""
    name: str
    module: Any
    config: Dict[str, Any]
    is_connected: bool = False
    last_active: float = 0


@dataclass
class ModelConnection:
    """服务端连接信息"""
    name: str
    module: Any
    config: Dict[str, Any]
    is_connected: bool = False
    last_used: float = 0
    concurrent_requests: int = 0
    max_concurrent_requests: int = 10


class PortManager:
    """端口与路由管理器"""
    
    def __init__(self, clients_dir: Path, models_dir: Path):
        """
        初始化端口管理器
        
        Args:
            clients_dir: 客户端目录
            models_dir: 服务端目录
        """
        self.logger = logging.getLogger(__name__)
        
        # 目录配置
        self.clients_dir = Path(clients_dir)
        self.models_dir = Path(models_dir)
        
        # 连接池
        self.client_connections = {}  # 客户端名称 -> ClientConnection
        self.model_connections = {}   # 服务端名称 -> ModelConnection
        
        # 消息回调
        self.message_callback = None
        
        # 配置
        self.config = None
        
        # 运行标志
        self.is_running = False
        
        # 锁
        self.lock = asyncio.Lock()
        self.model_lock = asyncio.Lock()
        
        # 活跃任务追踪
        self.active_tasks = set()
        
    async def initialize(self, config: Dict[str, Any], message_callback: Callable = None):
        """初始化端口管理器"""
        self.config = config
        self.message_callback = message_callback
        
        # 确保目录存在
        self.clients_dir.mkdir(exist_ok=True)
        self.models_dir.mkdir(exist_ok=True)
        
        # 扫描并加载客户端和服务端
        await self._scan_and_load_modules_async()
        
        self.is_running = True
        
        self.logger.info(f"端口管理器初始化完成，已加载 {len(self.client_connections)} 个客户端，{len(self.model_connections)} 个服务端")
        
    async def _scan_and_load_modules_async(self):
        """扫描并加载所有模块"""
        # 创建扫描任务
        scan_tasks = []
        
        # 加载客户端
        client_files = list(self.clients_dir.glob("*.py"))
        for client_file in client_files:
            task = asyncio.create_task(self._load_client_module_async(client_file))
            scan_tasks.append(task)
            
        # 加载服务端
        model_files = list(self.models_dir.glob("*.py"))
        for model_file in model_files:
            task = asyncio.create_task(self._load_model_module_async(model_file))
            scan_tasks.append(task)
            
        # 等待所有加载任务完成
        if scan_tasks:
            await asyncio.gather(*scan_tasks, return_exceptions=True)
                
    async def _load_client_module_async(self, client_file: Path):
        """加载客户端模块"""
        module_name = client_file.stem
        
        # 跳过非客户端文件
        if not module_name.endswith("_client"):
            return
            
        try:
            # 使用importlib导入模块
            spec = importlib.util.spec_from_file_location(module_name, str(client_file))
            if spec is None:
                self.logger.error(f"无法加载客户端模块: {module_name}")
                return
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 检查模块是否有必要的类
            if not hasattr(module, "Client"):
                self.logger.warning(f"客户端模块 {module_name} 没有Client类")
                return
                
            # 加载配置
            config_path = client_file.with_suffix('.json')
            config = {}
            if config_path.exists():
                try:
                    loop = asyncio.get_event_loop()
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = await loop.run_in_executor(None, json.load, f)
                except Exception as e:
                    self.logger.error(f"加载客户端配置失败 {config_path}: {e}")
                    
            # 创建客户端实例
            try:
                client_instance = module.Client()
                
                # 注册连接
                connection = ClientConnection(
                    name=module_name,
                    module=client_instance,
                    config=config,
                    is_connected=False
                )
                
                self.client_connections[module_name] = connection
                
                self.logger.info(f"客户端模块已加载: {module_name}")
                
            except Exception as e:
                self.logger.error(f"初始化客户端失败 {module_name}: {e}")
                
        except Exception as e:
            self.logger.error(f"导入客户端模块失败 {module_name}: {e}")
            
    async def _load_model_module_async(self, model_file: Path):
        """加载服务端模块"""
        module_name = model_file.stem
        
        # 跳过非服务端文件
        if not module_name.endswith("_model"):
            return
            
        try:
            # 使用importlib导入模块
            spec = importlib.util.spec_from_file_location(module_name, str(model_file))
            if spec is None:
                self.logger.error(f"无法加载服务端模块: {module_name}")
                return
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
                
            # 检查模块是否有必要的类
            if not hasattr(module, "Model"):
                self.logger.warning(f"服务端模块 {module_name} 没有Model类")
                return
                
            # 加载配置
            config_path = model_file.with_suffix('.json')
            config = {}
            if config_path.exists():
                try:
                    loop = asyncio.get_event_loop()
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = await loop.run_in_executor(None, json.load, f)
                except Exception as e:
                    self.logger.error(f"加载服务端配置失败 {config_path}: {e}")
                    
            # 创建服务端实例
            try:
                model_instance = module.Model()
                
                # 注册连接
                connection = ModelConnection(
                    name=module_name,
                    module=model_instance,
                    config=config,
                    is_connected=False,
                    max_concurrent_requests=config.get("performance", {}).get("max_concurrent_requests", 10)
                )
                
                self.model_connections[module_name] = connection
                
                self.logger.info(f"服务端模块已加载: {module_name}")
                
            except Exception as e:
                self.logger.error(f"初始化服务端失败 {module_name}: {e}")
                
        except Exception as e:
            self.logger.error(f"导入服务端模块失败 {module_name}: {e}")
            
    async def start(self):
        """启动所有连接"""
        self.logger.info("启动端口管理器连接...")
        
        # 创建启动任务
        start_tasks = []
        
        # 启动客户端连接
        for client_name, connection in list(self.client_connections.items()):
            task = asyncio.create_task(
                self._start_client_connection_async(client_name, connection)
            )
            start_tasks.append(task)
            
        # 启动服务端连接
        for model_name, connection in list(self.model_connections.items()):
            task = asyncio.create_task(
                self._start_model_connection_async(model_name, connection)
            )
            start_tasks.append(task)
            
        # 等待所有连接启动完成
        if start_tasks:
            await asyncio.gather(*start_tasks, return_exceptions=True)
                
    async def _start_client_connection_async(self, client_name: str, connection: ClientConnection):
        """启动客户端连接"""
        try:
            # 调用客户端的start方法
            if hasattr(connection.module, 'start'):
                await connection.module.start(
                    config=connection.config,
                    message_callback=self._handle_client_message_async
                )
                connection.is_connected = True
                connection.last_active = time.time()
                
                self.logger.info(f"客户端连接已启动: {client_name}")
                
                # 启动监控
                monitor_task = asyncio.create_task(
                    self._monitor_client_connection_async(client_name, connection)
                )
                self.active_tasks.add(monitor_task)
                monitor_task.add_done_callback(lambda t: self.active_tasks.discard(t))
                
        except Exception as e:
            self.logger.error(f"启动客户端 {client_name} 失败: {e}")
            
    async def _start_model_connection_async(self, model_name: str, connection: ModelConnection):
        """启动服务端连接"""
        try:
            # 调用服务端的start方法
            if hasattr(connection.module, 'start'):
                await connection.module.start(config=connection.config)
                connection.is_connected = True
                connection.last_used = time.time()
                
                self.logger.info(f"服务端连接已启动: {model_name}")
                
                # 启动监控
                monitor_task = asyncio.create_task(
                    self._monitor_model_connection_async(model_name, connection)
                )
                self.active_tasks.add(monitor_task)
                monitor_task.add_done_callback(lambda t: self.active_tasks.discard(t))
                
        except Exception as e:
            self.logger.error(f"启动服务端 {model_name} 失败: {e}")
            
    async def _monitor_client_connection_async(self, client_name: str, connection: ClientConnection):
        """监控客户端连接状态"""
        while self.is_running and connection.is_connected:
            try:
                await asyncio.sleep(30)  # 每30秒检查一次
                
                # 检查连接状态
                if hasattr(connection.module, 'is_connected_async'):
                    is_connected = await connection.module.is_connected_async()
                elif hasattr(connection.module, 'is_connected'):
                    attr = connection.module.is_connected
                    if callable(attr):
                        is_connected = await attr()
                    else:
                        is_connected = attr
                else:
                    is_connected = True
                        
                if not is_connected and connection.is_connected:
                    self.logger.warning(f"客户端连接断开: {client_name}")
                    connection.is_connected = False
                    
                    # 重连
                    await self._reconnect_client_async(client_name, connection)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"监控客户端连接失败 {client_name}: {e}")
                
    async def _monitor_model_connection_async(self, model_name: str, connection: ModelConnection):
        """监控服务端连接状态"""
        while self.is_running and connection.is_connected:
            try:
                await asyncio.sleep(30)  # 每30秒检查一次
                
                # 检查连接状态
                if hasattr(connection.module, 'is_connected_async'):
                    is_connected = await connection.module.is_connected_async()
                elif hasattr(connection.module, 'is_connected'):
                    attr = connection.module.is_connected
                    if callable(attr):
                        is_connected = await attr()
                    else:
                        is_connected = attr
                else:
                    is_connected = True
                        
                if not is_connected and connection.is_connected:
                    self.logger.warning(f"服务端连接断开: {model_name}")
                    connection.is_connected = False
                    
                    # 重连
                    await self._reconnect_model_async(model_name, connection)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"监控服务端连接失败 {model_name}: {e}")
                
    async def _reconnect_client_async(self, client_name: str, connection: ClientConnection):
        """重连客户端"""
        max_attempts = connection.config.get("connection", {}).get("max_reconnect_attempts", 3)
        reconnect_interval = connection.config.get("connection", {}).get("reconnect_interval", 5)
        
        for attempt in range(1, max_attempts + 1):
            try:
                self.logger.info(f"尝试重连客户端 {client_name} (第{attempt}次)...")
                
                await self._start_client_connection_async(client_name, connection)
                
                if connection.is_connected:
                    self.logger.info(f"客户端重连成功: {client_name}")
                    return
                    
            except Exception as e:
                self.logger.error(f"客户端重连失败 {client_name} (第{attempt}次): {e}")
                
            await asyncio.sleep(reconnect_interval)
            
        self.logger.error(f"客户端重连失败，已达最大重试次数: {client_name}")
        
    async def _reconnect_model_async(self, model_name: str, connection: ModelConnection):
        """重连服务端"""
        max_attempts = connection.config.get("connection", {}).get("max_reconnect_attempts", 3)
        reconnect_interval = connection.config.get("connection", {}).get("reconnect_interval", 5)
        
        for attempt in range(1, max_attempts + 1):
            try:
                self.logger.info(f"尝试重连服务端 {model_name} (第{attempt}次)...")
                
                await self._start_model_connection_async(model_name, connection)
                
                if connection.is_connected:
                    self.logger.info(f"服务端重连成功: {model_name}")
                    return
                    
            except Exception as e:
                self.logger.error(f"服务端重连失败 {model_name} (第{attempt}次): {e}")
                
            await asyncio.sleep(reconnect_interval)
            
        self.logger.error(f"服务端重连失败，已达最大重试次数: {model_name}")
        
    async def _handle_client_message_async(self, message_data: Dict[str, Any]):
        """处理来自客户端的消息"""
        if not self.message_callback:
            self.logger.warning("收到客户端消息，但消息回调未设置")
            return
            
        try:
            # 添加时间戳
            if "timestamp" not in message_data:
                message_data["timestamp"] = time.time()
                
            # 调用消息回调
            if asyncio.iscoroutinefunction(self.message_callback):
                await self.message_callback(message_data)
            else:
                # 同步回调，使用asyncio.to_thread执行
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: self.message_callback(message_data)
                )
                
        except Exception as e:
            self.logger.error(f"处理客户端消息失败: {e}")
            
    async def send_response_async(self, response_data: Dict[str, Any]):
        """
        发送消息到客户端
        
        Args:
            response_data: 响应数据
        """
        if not response_data or "chat_id" not in response_data:
            self.logger.warning(f"无效的响应数据: {response_data}")
            return
            
        try:
            chat_id = response_data["chat_id"]
            self.logger.info(f"端口管理器发送响应到: {chat_id}")
            
            # 创建发送任务
            send_tasks = []
            
            # 获取客户端连接
            for client_name, connection in list(self.client_connections.items()):
                if connection.is_connected:
                    # 创建发送任务
                    task = asyncio.create_task(
                        self._send_message_to_client_async(connection, response_data)
                    )
                    send_tasks.append(task)
                else:
                    self.logger.warning(f"客户端 {client_name} 未连接，无法发送响应")
                    
            # 等待所有发送任务完成
            if send_tasks:
                await asyncio.gather(*send_tasks, return_exceptions=True)
                    
        except Exception as e:
            self.logger.error(f"发送响应失败: {e}")
            
    async def _send_message_to_client_async(self, connection: ClientConnection, response_data: Dict[str, Any]):
        """发送消息到客户端"""
        try:
            if hasattr(connection.module, 'send_message_async'):
                await connection.module.send_message_async(response_data)
                connection.last_active = time.time()
                self.logger.debug(f"消息通过方式发送成功: {connection.name}")
            else:
                self.logger.error(f"客户端 {connection.name} 没有send_message_async方法")
                
        except Exception as e:
            self.logger.error(f"发送消息到客户端失败 {connection.name}: {e}")
            
    async def send_to_model_async(self, request_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        发送请求到服务端
        
        Args:
            request_data: 请求数据
            
        Returns:
            服务端响应
        """
        if not request_data:
            return None
            
        try:
            # 选择可用的服务端
            selected_model = None
            async with self.model_lock:
                for model_name, connection in self.model_connections.items():
                    if connection.is_connected and connection.concurrent_requests < connection.max_concurrent_requests:
                        selected_model = connection
                        connection.concurrent_requests += 1
                        connection.last_used = time.time()
                        break
                        
            if not selected_model:
                self.logger.error("没有可用的服务端")
                return None
                
            try:
                # 调用服务端的send_request_async方法
                if hasattr(selected_model.module, 'send_request_async'):
                    response = await selected_model.module.send_request_async(request_data)
                    return response
                else:
                    self.logger.error(f"服务端 {selected_model.name} 没有send_request_async方法")
                    return None
                    
            finally:
                # 减少并发请求计数
                async with self.model_lock:
                    selected_model.concurrent_requests -= 1
                    
        except Exception as e:
            self.logger.error(f"发送到服务端失败: {e}")
            return None
            
    async def get_status_async(self) -> Dict[str, Any]:
        """获取端口管理器状态"""
        status = {
            "is_running": self.is_running,
            "active_tasks": len(self.active_tasks),
            "clients": {},
            "models": {}
        }
        
        async with self.lock:
            for client_name, connection in self.client_connections.items():
                status["clients"][client_name] = {
                    "is_connected": connection.is_connected,
                    "last_active": connection.last_active
                }
                
            for model_name, connection in self.model_connections.items():
                status["models"][model_name] = {
                    "is_connected": connection.is_connected,
                    "last_used": connection.last_used,
                    "concurrent_requests": connection.concurrent_requests,
                    "max_concurrent_requests": connection.max_concurrent_requests
                }
                
        return status
        
    async def stop(self):
        """停止所有连接"""
        self.logger.info("停止端口管理器...")
        
        self.is_running = False
        
        # 取消所有活跃任务
        for task in self.active_tasks:
            task.cancel()
            
        # 等待任务取消完成
        if self.active_tasks:
            await asyncio.gather(*self.active_tasks, return_exceptions=True)
            
        # 停止客户端连接
        stop_tasks = []
        for client_name, connection in self.client_connections.items():
            task = asyncio.create_task(
                self._stop_client_connection_async(client_name, connection)
            )
            stop_tasks.append(task)
            
        # 停止服务端连接
        for model_name, connection in self.model_connections.items():
            task = asyncio.create_task(
                self._stop_model_connection_async(model_name, connection)
            )
            stop_tasks.append(task)
            
        # 等待所有停止任务完成
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
            
        self.logger.info("端口管理器已停止")
        
    async def _stop_client_connection_async(self, client_name: str, connection: ClientConnection):
        """停止客户端连接"""
        try:
            if connection.is_connected and hasattr(connection.module, 'stop'):
                await connection.module.stop()
                connection.is_connected = False
                
            self.logger.info(f"客户端连接已停止: {client_name}")
        except Exception as e:
            self.logger.error(f"停止客户端连接失败 {client_name}: {e}")
            
    async def _stop_model_connection_async(self, model_name: str, connection: ModelConnection):
        """停止服务端连接"""
        try:
            if connection.is_connected and hasattr(connection.module, 'stop'):
                await connection.module.stop()
                connection.is_connected = False
                
            self.logger.info(f"服务端连接已停止: {model_name}")
        except Exception as e:
            self.logger.error(f"停止服务端连接失败 {model_name}: {e}")