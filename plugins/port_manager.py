# port_manager.py
#!/usr/bin/env python3

import importlib
import logging
import asyncio
import json
import time
from pathlib import Path

class ClientConnection:
    def __init__(self, name, module, config):
        self.name = name
        self.module = module
        self.config = config
        self.is_connected = False
        self.last_active = 0

class ModelConnection:
    def __init__(self, name, module, config):
        self.name = name
        self.module = module
        self.config = config
        self.is_connected = False
        self.last_used = 0
        self.concurrent_requests = 0
        self.max_concurrent_requests = config.get("performance", {}).get("max_concurrent_requests", 10)

class PortManager:
    def __init__(self, clients_dir, models_dir):
        self.logger = logging.getLogger(__name__)
        self.clients_dir = Path(clients_dir)
        self.models_dir = Path(models_dir)
        self.client_connections = {}
        self.model_connections = {}
        self.message_callback = None
        self.config = None
        self.is_running = False
        self.lock = asyncio.Lock()
        self.model_lock = asyncio.Lock()
        self.active_tasks = set()
        
    async def initialize(self, config, message_callback=None):
        self.config = config
        self.message_callback = message_callback
        
        self.clients_dir.mkdir(exist_ok=True)
        self.models_dir.mkdir(exist_ok=True)
        
        await self._scan_and_load_modules_async()
        self.is_running = True
        
    async def _scan_and_load_modules_async(self):
        scan_tasks = []
        
        client_files = list(self.clients_dir.glob("*.py"))
        for client_file in client_files:
            task = asyncio.create_task(self._load_client_module_async(client_file))
            scan_tasks.append(task)
            
        model_files = list(self.models_dir.glob("*.py"))
        for model_file in model_files:
            task = asyncio.create_task(self._load_model_module_async(model_file))
            scan_tasks.append(task)
            
        if scan_tasks:
            await asyncio.gather(*scan_tasks, return_exceptions=True)
                
    async def _load_client_module_async(self, client_file):
        module_name = client_file.stem
        if not module_name.endswith("_client"):
            return
            
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(client_file))
            if spec is None:
                return
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            if not hasattr(module, "Client"):
                return
                
            config_path = client_file.with_suffix('.json')
            config = {}
            if config_path.exists():
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                except Exception as e:
                    self.logger.error(f"加载客户端配置失败 {config_path}: {e}")
                    
            client_instance = module.Client()
            connection = ClientConnection(
                name=module_name,
                module=client_instance,
                config=config
            )
            
            self.client_connections[module_name] = connection
            
        except Exception as e:
            self.logger.error(f"导入客户端模块失败 {module_name}: {e}")
            
    async def _load_model_module_async(self, model_file):
        module_name = model_file.stem
        if not module_name.endswith("_model"):
            return
            
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(model_file))
            if spec is None:
                return
                
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
                
            if not hasattr(module, "Model"):
                return
                
            config_path = model_file.with_suffix('.json')
            config = {}
            if config_path.exists():
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                except Exception as e:
                    self.logger.error(f"加载服务端配置失败 {config_path}: {e}")
                    
            model_instance = module.Model()
            connection = ModelConnection(
                name=module_name,
                module=model_instance,
                config=config
            )
            
            self.model_connections[module_name] = connection
            
        except Exception as e:
            self.logger.error(f"导入服务端模块失败 {module_name}: {e}")
            
    async def start(self):
        start_tasks = []
        
        for client_name, connection in list(self.client_connections.items()):
            task = asyncio.create_task(self._start_client_connection_async(client_name, connection))
            start_tasks.append(task)
            
        for model_name, connection in list(self.model_connections.items()):
            task = asyncio.create_task(self._start_model_connection_async(model_name, connection))
            start_tasks.append(task)
            
        if start_tasks:
            await asyncio.gather(*start_tasks, return_exceptions=True)
                
    async def _start_client_connection_async(self, client_name, connection):
        try:
            if hasattr(connection.module, 'start'):
                await connection.module.start(
                    config=connection.config,
                    message_callback=self._handle_client_message_async
                )
                connection.is_connected = True
                connection.last_active = time.time()
                
                monitor_task = asyncio.create_task(self._monitor_client_connection_async(client_name, connection))
                self.active_tasks.add(monitor_task)
                monitor_task.add_done_callback(lambda t: self.active_tasks.discard(t))
                
        except Exception as e:
            self.logger.error(f"启动客户端 {client_name} 失败: {e}")
            
    async def _start_model_connection_async(self, model_name, connection):
        try:
            if hasattr(connection.module, 'start'):
                await connection.module.start(config=connection.config)
                connection.is_connected = True
                connection.last_used = time.time()
                
                monitor_task = asyncio.create_task(self._monitor_model_connection_async(model_name, connection))
                self.active_tasks.add(monitor_task)
                monitor_task.add_done_callback(lambda t: self.active_tasks.discard(t))
                
        except Exception as e:
            self.logger.error(f"启动服务端 {model_name} 失败: {e}")
            
    async def _monitor_client_connection_async(self, client_name, connection):
        while self.is_running and connection.is_connected:
            try:
                await asyncio.sleep(30)
                
                is_connected = True
                if hasattr(connection.module, 'is_connected_async'):
                    is_connected = await connection.module.is_connected_async()
                elif hasattr(connection.module, 'is_connected'):
                    attr = connection.module.is_connected
                    if callable(attr):
                        is_connected = await attr()
                    else:
                        is_connected = attr
                        
                if not is_connected and connection.is_connected:
                    connection.is_connected = False
                    await self._reconnect_client_async(client_name, connection)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"监控客户端连接失败 {client_name}: {e}")
                
    async def _monitor_model_connection_async(self, model_name, connection):
        while self.is_running and connection.is_connected:
            try:
                await asyncio.sleep(30)
                
                is_connected = True
                if hasattr(connection.module, 'is_connected_async'):
                    is_connected = await connection.module.is_connected_async()
                elif hasattr(connection.module, 'is_connected'):
                    attr = connection.module.is_connected
                    if callable(attr):
                        is_connected = await attr()
                    else:
                        is_connected = attr
                        
                if not is_connected and connection.is_connected:
                    connection.is_connected = False
                    await self._reconnect_model_async(model_name, connection)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"监控服务端连接失败 {model_name}: {e}")
                
    async def _reconnect_client_async(self, client_name, connection):
        max_attempts = connection.config.get("connection", {}).get("max_reconnect_attempts", 3)
        reconnect_interval = connection.config.get("connection", {}).get("reconnect_interval", 5)
        
        for attempt in range(1, max_attempts + 1):
            try:
                await self._start_client_connection_async(client_name, connection)
                if connection.is_connected:
                    return
            except Exception as e:
                self.logger.error(f"客户端重连失败 {client_name} (第{attempt}次): {e}")
            await asyncio.sleep(reconnect_interval)
            
    async def _reconnect_model_async(self, model_name, connection):
        max_attempts = connection.config.get("connection", {}).get("max_reconnect_attempts", 3)
        reconnect_interval = connection.config.get("connection", {}).get("reconnect_interval", 5)
        
        for attempt in range(1, max_attempts + 1):
            try:
                await self._start_model_connection_async(model_name, connection)
                if connection.is_connected:
                    return
            except Exception as e:
                self.logger.error(f"服务端重连失败 {model_name} (第{attempt}次): {e}")
            await asyncio.sleep(reconnect_interval)
            
    async def _handle_client_message_async(self, message_data):
        if not self.message_callback:
            return
            
        try:
            if "timestamp" not in message_data:
                message_data["timestamp"] = time.time()
                
            if asyncio.iscoroutinefunction(self.message_callback):
                await self.message_callback(message_data)
            else:
                await asyncio.get_event_loop().run_in_executor(None, lambda: self.message_callback(message_data))
                
        except Exception as e:
            self.logger.error(f"处理客户端消息失败: {e}")
            
    async def send_response_async(self, response_data):
        if not response_data or "chat_id" not in response_data:
            return
            
        send_tasks = []
        
        for client_name, connection in list(self.client_connections.items()):
            if connection.is_connected:
                task = asyncio.create_task(self._send_message_to_client_async(connection, response_data))
                send_tasks.append(task)
                    
        if send_tasks:
            await asyncio.gather(*send_tasks, return_exceptions=True)
                    
    async def _send_message_to_client_async(self, connection, response_data):
        try:
            if hasattr(connection.module, 'send_message_async'):
                await connection.module.send_message_async(response_data)
                connection.last_active = time.time()
        except Exception as e:
            self.logger.error(f"发送消息到客户端失败 {connection.name}: {e}")
            
    async def send_to_model_async(self, request_data):
        if not request_data:
            return None
            
        selected_model = None
        async with self.model_lock:
            for model_name, connection in self.model_connections.items():
                if connection.is_connected and connection.concurrent_requests < connection.max_concurrent_requests:
                    selected_model = connection
                    connection.concurrent_requests += 1
                    connection.last_used = time.time()
                    break
                        
        if not selected_model:
            return None
                
        try:
            if hasattr(selected_model.module, 'send_request_async'):
                return await selected_model.module.send_request_async(request_data)
            return None
        finally:
            async with self.model_lock:
                selected_model.concurrent_requests -= 1
                    
    async def get_status_async(self):
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
        self.is_running = False
        
        for task in self.active_tasks:
            task.cancel()
            
        if self.active_tasks:
            await asyncio.gather(*self.active_tasks, return_exceptions=True)
            
        stop_tasks = []
        for client_name, connection in self.client_connections.items():
            task = asyncio.create_task(self._stop_client_connection_async(client_name, connection))
            stop_tasks.append(task)
            
        for model_name, connection in self.model_connections.items():
            task = asyncio.create_task(self._stop_model_connection_async(model_name, connection))
            stop_tasks.append(task)
            
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
            
    async def _stop_client_connection_async(self, client_name, connection):
        try:
            if connection.is_connected and hasattr(connection.module, 'stop'):
                await connection.module.stop()
                connection.is_connected = False
        except Exception as e:
            self.logger.error(f"停止客户端连接失败 {client_name}: {e}")
            
    async def _stop_model_connection_async(self, model_name, connection):
        try:
            if connection.is_connected and hasattr(connection.module, 'stop'):
                await connection.module.stop()
                connection.is_connected = False
        except Exception as e:
            self.logger.error(f"停止服务端连接失败 {model_name}: {e}")