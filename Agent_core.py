#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent_core.py - 异步系统核心调度器
集成重构后的工具调用系统
"""

import asyncio
import signal
import sys
import logging
import time
from pathlib import Path
from typing import Dict, Any

from plugins.config_manager import ConfigManager
from plugins.context_manager import ContextManager
from plugins.queue_manager import QueueManager
from plugins.task_manager import TaskManager
from plugins.rules_manager import RulesManager
from plugins.session_manager import SessionManager
from plugins.tool_manager import ToolManager
from plugins.essentials_manager import EssentialsManager
from plugins.port_manager import PortManager
from plugins.image_manager import ImageManager


class AgentCore:
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.plugins_dir = self.base_dir / "plugins"
        self.clients_dir = self.base_dir / "clients"
        self.models_dir = self.base_dir / "models"
        self.chat_dir = self.base_dir / "chat"
        self.history_dir = self.chat_dir / "history"
        self.tools_service_dir = self.base_dir / "tools_service"
        
        self.config_manager = None
        self.context_manager = None
        self.queue_manager = None
        self.task_manager = None
        self.rules_manager = None
        self.session_manager = None
        self.tool_manager = None
        self.essentials_manager = None
        self.port_manager = None
        self.image_manager = None
        
        self.is_running = False
        self.shutdown_requested = False
        
        self._setup_logging()
        
    def _setup_logging(self):
        log_dir = self.base_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / "agent_core.log", encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def _create_directories(self):
        directories = [
            self.plugins_dir,
            self.clients_dir,
            self.models_dir,
            self.chat_dir,
            self.history_dir,
            self.tools_service_dir,
            self.base_dir / "logs"
        ]
        
        for directory in directories:
            directory.mkdir(exist_ok=True)
            
    async def _initialize_modules(self):
        try:
            self._create_directories()
            
            self.logger.info("初始化配置管理器...")
            self.config_manager = ConfigManager(self.plugins_dir)
            config = await self.config_manager.initialize()
            
            self.logger.info("初始化图片管理器...")
            self.image_manager = ImageManager()
            await self.image_manager.initialize()
            
            self.logger.info("初始化上下文管理器...")
            self.context_manager = ContextManager(self.history_dir)
            await self.context_manager.initialize(config)
            
            self.logger.info("初始化工具管理器...")
            self.tool_manager = ToolManager(self.tools_service_dir)
            
            # 先设置上下文管理器引用
            self.tool_manager.set_context_manager(self.context_manager)
            
            # 然后初始化工具管理器
            await self.tool_manager.initialize(config)
            
            # 注入上下文管理器到已加载的模块
            await self.tool_manager.inject_context_to_modules()
            
            self.context_manager.set_tool_manager(self.tool_manager)
            
            self.logger.info("初始化异步队列管理器...")
            self.queue_manager = QueueManager()
            await self.queue_manager.initialize(config)
            
            self.logger.info("初始化异步任务调度器...")
            self.task_manager = TaskManager()
            await self.task_manager.initialize(
                config=config,
                context_manager=self.context_manager,
                session_manager=None,
                essentials_manager=None,
                tool_manager=self.tool_manager,
                port_manager=None,
                message_callback=self._handle_message_result
            )
            
            self.logger.info("初始化异步会话管理器...")
            self.session_manager = SessionManager()
            await self.session_manager.initialize(config)
            self.session_manager.set_image_manager(self.image_manager)
            
            self.logger.info("初始化基础指令处理器...")
            self.essentials_manager = EssentialsManager()
            await self.essentials_manager.initialize(
                config=config,
                context_manager=self.context_manager,
                tool_manager=self.tool_manager
            )
            
            self.logger.info("初始化异步规则管理器...")
            self.rules_manager = RulesManager()
            await self.rules_manager.initialize(
                config=config,
                queue_manager=self.queue_manager,
                task_manager=self.task_manager
            )
            
            self.logger.info("初始化异步端口管理器...")
            self.port_manager = PortManager(self.clients_dir, self.models_dir)
            await self.port_manager.initialize(
                config=config,
                message_callback=self._handle_incoming_message
            )
            
            # 更新任务管理器的引用
            self.task_manager.session_manager = self.session_manager
            self.task_manager.essentials_manager = self.essentials_manager
            self.task_manager.port_manager = self.port_manager
            
            self.logger.info("所有异步模块初始化完成")
            
        except Exception as e:
            self.logger.error(f"异步模块初始化失败: {e}")
            raise
            
    async def _handle_incoming_message(self, message_data: Dict[str, Any]):
        if not self.is_running:
            return
            
        if not message_data or "chat_id" not in message_data:
            return
            
        chat_id = message_data["chat_id"]
        
        if self.image_manager:
            try:
                analysis_result = await self.image_manager.analyze_message(message_data)
                if analysis_result.get("success") and analysis_result.get("has_images"):
                    image_count = analysis_result.get("image_count", 0)
                    self.logger.debug(f"消息包含 {image_count} 张图片")
            except Exception as e:
                self.logger.error(f"分析消息图片失败: {e}")
        
        task_id = await self.queue_manager.enqueue_message(
            chat_id=chat_id,
            task_data={
                **message_data,
                "source": "client",
                "timestamp": time.time()
            }
        )
        
        if task_id:
            self.logger.debug(f"消息已加入异步队列: {chat_id}, task_id={task_id}")
            
    async def _handle_message_result(self, result: Dict[str, Any]):
        if not self.is_running:
            return
            
        workflow_type = result.get("workflow_type")
        chat_id = result.get("chat_id")
        
        if workflow_type == "A":
            if "response" in result:
                await self._send_response(result["response"])
        elif workflow_type == "B":
            await self.rules_manager.handle_workflow_b_result(result)
        elif workflow_type == "C":
            success = result.get("success", False)
            
            if success and "response" in result:
                response_data = result["response"]
                
                if "chat_id" not in response_data and chat_id:
                    response_data["chat_id"] = chat_id
                
                await self._send_response(response_data)
                
                await self._add_ai_reply_to_context(
                    chat_id=chat_id,
                    response=response_data
                )
            else:
                error_msg = result.get("error", "未知错误")
                error_response = {
                    "chat_id": chat_id,
                    "content": f"处理消息时发生错误: {error_msg}",
                    "timestamp": time.time()
                }
                await self._send_response(error_response)
                
    async def _send_response(self, response_data: Dict[str, Any]):
        if self.port_manager and response_data and "chat_id" in response_data:
            await self.port_manager.send_response_async(response_data)
            
    async def _add_ai_reply_to_context(self, chat_id: str, response: Dict[str, Any]):
        if not chat_id or not response:
            return
            
        reply_content = response.get("content", "")
        if not reply_content:
            return
            
        ai_message = {
            "role": "assistant",
            "content": reply_content
        }
        
        await self.queue_manager.enqueue_message(
            chat_id=chat_id,
            task_data={
                "chat_id": chat_id,
                "message": ai_message,
                "role": "assistant",
                "is_respond": False,
                "timestamp": time.time()
            }
        )
        
    async def _start_queue_consumers(self):
        self.queue_manager.set_task_callback(self._handle_queue_task)
        self.queue_manager.set_message_callback(self._handle_message_result)
        await self.queue_manager.start()
        
    async def _handle_queue_task(self, task_info: Dict[str, Any]) -> Dict[str, Any]:
        if self.task_manager:
            return await self.task_manager.execute_task(task_info)
        return {"success": False, "error": "task_manager未初始化"}
            
    async def _run_event_loop(self):
        if self.port_manager:
            await self.port_manager.start()
            
        await self._start_queue_consumers()
            
        while self.is_running and not self.shutdown_requested:
            await asyncio.sleep(1)
            
    async def start(self):
        if self.is_running:
            return
            
        self.logger.info("正在启动异步跨平台Agent系统...")
        
        try:
            await self._initialize_modules()
            self._setup_signal_handlers()
            self.is_running = True
            await self._run_event_loop()
            
        except Exception as e:
            self.logger.error(f"系统启动失败: {e}")
            await self.stop()
            
    async def stop(self):
        self.logger.info("正在停止异步系统...")
        self.shutdown_requested = True
        self.is_running = False
        
        if self.port_manager:
            await self.port_manager.stop()
            
        if self.queue_manager:
            await self.queue_manager.shutdown()
            
        if self.image_manager:
            await self.image_manager.shutdown()
            
        if self.context_manager:
            await self.context_manager.shutdown()
            
        if self.session_manager:
            await self.session_manager.shutdown()
            
        if self.task_manager:
            # 清理任务管理器的工具跟踪状态
            await self.task_manager.cleanup_session_tools("*")
            
    def _setup_signal_handlers(self):
        if sys.platform != "win32":
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            
    def _signal_handler(self, signum, frame):
        asyncio.create_task(self.stop())


async def main():
    agent = AgentCore()
    try:
        await agent.start()
    except KeyboardInterrupt:
        print("\n收到中断信号，正在关闭...")
    except Exception as e:
        print(f"系统异常: {e}")
    finally:
        await agent.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序已终止")
    except Exception as e:
        print(f"程序异常退出: {e}")
        sys.exit(1)