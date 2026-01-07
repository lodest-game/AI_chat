#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent_core.py - 异步系统核心调度器
基于asyncio的完全异步架构，支持高并发处理
增加了image_manager模块
"""

import asyncio
import signal
import sys
import logging
import traceback
import time
from pathlib import Path
from typing import Dict, Any, Optional, Callable
import uuid

# 导入模块
from plugins.config_manager import ConfigManager
from plugins.context_manager import ContextManager
from plugins.queue_manager import QueueManager
from plugins.task_manager import TaskManager
from plugins.rules_manager import RulesManager
from plugins.session_manager import SessionManager
from plugins.tool_manager import ToolManager
from plugins.essentials_manager import EssentialsManager
from plugins.port_manager import PortManager
from plugins.image_manager import ImageManager  # 新增导入


class AgentCore:
    """异步系统核心调度器"""
    
    def __init__(self):
        """初始化Agent核心"""
        # 基础目录结构
        self.base_dir = Path(__file__).parent
        self.plugins_dir = self.base_dir / "plugins"
        self.clients_dir = self.base_dir / "clients"
        self.models_dir = self.base_dir / "models"
        self.chat_dir = self.base_dir / "chat"
        self.history_dir = self.chat_dir / "history"
        self.tools_service_dir = self.base_dir / "tools_service"
        
        # 模块实例
        self.config_manager = None
        self.context_manager = None
        self.queue_manager = None
        self.task_manager = None
        self.rules_manager = None
        self.session_manager = None
        self.tool_manager = None
        self.essentials_manager = None
        self.port_manager = None
        self.image_manager = None  # 新增图片管理器
        
        # 系统状态
        self.is_running = False
        self.shutdown_requested = False
        
        # 聊天队列映射（chat_id -> 处理任务）
        self.chat_queues = {}
        self.chat_queue_tasks = {}
        
        # 日志配置
        self._setup_logging()
        
        # 消息处理回调
        self.message_callback = None
        
    def _setup_logging(self):
        """配置日志系统"""
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
        """创建必要目录"""
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
            self.logger.info(f"目录已创建/确认: {directory}")
            
        # 确保system.json存在
        system_json = self.plugins_dir / "system.json"
        if not system_json.exists():
            self.logger.info("未发现system.json配置文件，将使用默认配置")
            
    async def _initialize_modules(self):
        """异步初始化所有模块"""
        try:
            # 1. 创建目录
            self._create_directories()
            
            # 2. 初始化配置管理器
            self.logger.info("正在初始化配置管理器...")
            self.config_manager = ConfigManager(self.plugins_dir)
            config = await self.config_manager.initialize()
            
            # 3. 初始化图片管理器（新增，在队列管理器之前）
            self.logger.info("正在初始化图片管理器...")
            self.image_manager = ImageManager()
            await self.image_manager.initialize()
            
            # 4. 初始化上下文管理器
            self.logger.info("正在初始化上下文管理器...")
            self.context_manager = ContextManager(self.history_dir)
            await self.context_manager.initialize(config)
            
            # 5. 初始化工具管理器
            self.logger.info("正在初始化工具管理器...")
            self.tool_manager = ToolManager(self.tools_service_dir)
            await self.tool_manager.initialize(config)
            
            # 6. 设置工具管理器到上下文管理器
            self.context_manager.set_tool_manager(self.tool_manager)
            
            # 7. 设置上下文管理器到工具管理器
            self.tool_manager.set_context_manager(self.context_manager)
            
            # 8. 初始化队列管理器（异步）
            self.logger.info("正在初始化异步队列管理器...")
            self.queue_manager = QueueManager()
            await self.queue_manager.initialize(config)
            
            # 9. 初始化任务调度器
            self.logger.info("正在初始化异步任务调度器...")
            self.task_manager = TaskManager()
            await self.task_manager.initialize(
                config=config,
                context_manager=self.context_manager,
                session_manager=None,  # 稍后设置
                essentials_manager=None,  # 稍后设置
                tool_manager=self.tool_manager,
                port_manager=None,  # 稍后设置
                message_callback=self._handle_message_result
            )
            
            # 10. 初始化会话管理器
            self.logger.info("正在初始化异步会话管理器...")
            self.session_manager = SessionManager()
            await self.session_manager.initialize(config)
            
            # 11. 设置图片管理器到会话管理器（新增）
            self.session_manager.set_image_manager(self.image_manager)
            
            # 12. 初始化基础指令处理器
            self.logger.info("正在初始化基础指令处理器...")
            self.essentials_manager = EssentialsManager()
            await self.essentials_manager.initialize(
                config=config,
                context_manager=self.context_manager,
                tool_manager=self.tool_manager
            )
            
            # 13. 初始化规则管理器
            self.logger.info("正在初始化异步规则管理器...")
            self.rules_manager = RulesManager()
            await self.rules_manager.initialize(
                config=config,
                queue_manager=self.queue_manager,
                task_manager=self.task_manager
            )
            
            # 14. 初始化端口管理器
            self.logger.info("正在初始化异步端口管理器...")
            self.port_manager = PortManager(self.clients_dir, self.models_dir)
            await self.port_manager.initialize(
                config=config,
                message_callback=self._handle_incoming_message_with_images  # 修改回调函数
            )
            
            # 15. 更新模块间的引用
            self.task_manager.session_manager = self.session_manager
            self.task_manager.essentials_manager = self.essentials_manager
            self.task_manager.port_manager = self.port_manager
            
            self.logger.info("所有异步模块初始化完成")
            
        except Exception as e:
            self.logger.error(f"异步模块初始化失败: {e}")
            self.logger.error(traceback.format_exc())
            raise
            
    async def _handle_incoming_message_with_images(self, message_data: Dict[str, Any]):
        """
        处理来自客户端的消息（增加图片分析）
        
        Args:
            message_data: 消息数据
        """
        if not self.is_running:
            self.logger.warning("系统未运行，忽略消息")
            return
            
        try:
            # 验证消息数据
            if not message_data or "chat_id" not in message_data:
                self.logger.warning("收到无效消息，缺少chat_id")
                return
                
            chat_id = message_data["chat_id"]
            self.logger.info(f"收到消息: chat_id={chat_id}")
            
            # =========== 新增：分析消息中的图片 ===========
            if self.image_manager:
                try:
                    # 异步分析消息中的图片URL
                    analysis_result = await self.image_manager.analyze_message(message_data)
                    
                    if analysis_result.get("success") and analysis_result.get("has_images"):
                        image_count = analysis_result.get("image_count", 0)
                        self.logger.info(f"消息包含 {image_count} 张图片，已开始异步处理")
                except Exception as e:
                    self.logger.error(f"分析消息图片失败: {e}")
            # ============================================
            
            # 将消息提交到队列管理器
            task_id = await self.queue_manager.enqueue_message(
                chat_id=chat_id,
                task_data={
                    **message_data,
                    "source": "client",
                    "timestamp": time.time()
                }
            )
            
            if task_id:
                self.logger.debug(f"消息已加入异步队列: chat_id={chat_id}, task_id={task_id}")
            else:
                self.logger.warning(f"消息加入异步队列失败: chat_id={chat_id}")
                
        except Exception as e:
            self.logger.error(f"异步处理消息失败: {e}")
            self.logger.error(traceback.format_exc())
            
    async def _handle_incoming_message(self, message_data: Dict[str, Any]):
        """异步处理来自客户端的消息（保持原函数，兼容性）"""
        await self._handle_incoming_message_with_images(message_data)
            
    async def _handle_message_result(self, result: Dict[str, Any]):
        """异步处理任务处理结果"""
        if not self.is_running:
            self.logger.warning("系统未运行，忽略结果")
            return
            
        try:
            workflow_type = result.get("workflow_type")
            chat_id = result.get("chat_id")
            
            if workflow_type == "A":
                # 工作流A直接输出
                if "response" in result:
                    self.logger.info(f"工作流A准备发送响应: {chat_id}")
                    await self._send_response(result["response"])
                    
            elif workflow_type == "B":
                # 工作流B提交给规则管理器
                self.logger.info(f"处理工作流B结果: chat_id={chat_id}")
                await self.rules_manager.handle_workflow_b_result(result)
                
            elif workflow_type == "C":
                # 工作流C发送响应并更新上下文
                success = result.get("success", False)
                
                self.logger.info(f"处理工作流C结果: chat_id={chat_id}, success={success}")
                
                if success and "response" in result:
                    response_data = result["response"]
                    
                    # 确保response_data包含chat_id
                    if "chat_id" not in response_data and chat_id:
                        response_data["chat_id"] = chat_id
                    
                    # 记录响应内容
                    content_preview = str(response_data.get("content", ""))[:100]
                    self.logger.info(f"工作流C准备发送响应: chat_id={chat_id}, content_preview={content_preview}...")
                    
                    # 发送响应
                    await self._send_response(response_data)
                    
                    # 将AI回复添加到对话历史
                    await self._add_ai_reply_to_context(
                        chat_id=chat_id,
                        response=response_data
                    )
                else:
                    # 处理失败情况
                    error_msg = result.get("error", "未知错误")
                    self.logger.error(f"工作流C执行失败: chat_id={chat_id}, error={error_msg}")
                    
                    # 发送错误消息给用户
                    error_response = {
                        "chat_id": chat_id,
                        "content": f"处理消息时发生错误: {error_msg}",
                        "timestamp": time.time()
                    }
                    await self._send_response(error_response)
                    
            else:
                self.logger.warning(f"未知的工作流类型: {workflow_type}")
                
        except Exception as e:
            self.logger.error(f"处理结果失败: {e}")
            self.logger.error(traceback.format_exc())
            
    async def _send_response(self, response_data: Dict[str, Any]):
        """异步发送响应到客户端"""
        try:
            if self.port_manager and response_data:
                # 确保response_data包含chat_id
                if "chat_id" not in response_data:
                    self.logger.warning(f"响应数据缺少chat_id: {response_data}")
                    return
                    
                await self.port_manager.send_response_async(response_data)
        except Exception as e:
            self.logger.error(f"发送响应失败: {e}")
            
    async def _add_ai_reply_to_context(self, chat_id: str, response: Dict[str, Any]):
        """将AI回复添加到对话上下文"""
        try:
            if not chat_id or not response:
                return
                
            # 提取回复内容
            reply_content = response.get("content", "")
            if not reply_content:
                return
                
            # 构建AI消息
            ai_message = {
                "role": "assistant",
                "content": reply_content
            }
            
            # 创建AI回复任务 - 使用标准协议字段
            await self.queue_manager.enqueue_message(
                chat_id=chat_id,
                task_data={
                    "chat_id": chat_id,
                    "message": ai_message,
                    "role": "assistant",  # 明确角色
                    "is_respond": False,  # 标准协议：不需要模型响应
                    "timestamp": time.time()
                }
            )
            
            self.logger.info(f"AI回复已加入队列等待更新上下文: {chat_id}, content_preview={reply_content[:50]}...")
            
        except Exception as e:
            self.logger.error(f"添加AI回复到上下文失败: {e}")
            
        except Exception as e:
            self.logger.error(f"添加AI回复到上下文失败: {e}")
            
    async def _start_queue_consumers(self):
        """启动队列消费者任务"""
        self.logger.info("启动异步队列消费者任务...")
        
        # 设置队列处理回调
        self.queue_manager.set_task_callback(self._handle_queue_task)
        self.queue_manager.set_message_callback(self._handle_message_result)
        
        # 启动队列管理器
        await self.queue_manager.start()
        
    async def _handle_queue_task(self, task_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        异步处理队列任务
        
        Args:
            task_info: 任务信息
            
        Returns:
            任务执行结果
        """
        try:
            self.logger.info(f"处理异步队列任务: task_id={task_info.get('task_id')}, chat_id={task_info.get('chat_id')}")
            
            # 将任务交给task_manager执行
            if self.task_manager:
                result = await self.task_manager.execute_task(task_info)
                return result
            else:
                self.logger.error("task_manager未初始化")
                return {
                    "success": False,
                    "error": "task_manager未初始化"
                }
                
        except Exception as e:
            self.logger.error(f"处理队列任务失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def _run_event_loop(self):
        """运行异步事件循环"""
        self.logger.info("异步事件循环启动")
        
        try:
            # 启动端口管理器
            if self.port_manager:
                await self.port_manager.start()
                
            # 启动队列消费者
            await self._start_queue_consumers()
                
            # 主循环
            while self.is_running and not self.shutdown_requested:
                try:
                    # 异步等待
                    await asyncio.sleep(1)
                    
                    # 这里可以添加定期检查任务
                    # 例如：清理过期会话、检查连接状态等
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"主循环错误: {e}")
                    
        except Exception as e:
            self.logger.error(f"事件循环失败: {e}")
            self.logger.error(traceback.format_exc())
            
    async def start(self):
        """异步启动系统"""
        if self.is_running:
            self.logger.warning("系统已经在运行中")
            return
            
        self.logger.info("正在启动异步跨平台Agent系统...")
        
        try:
            # 初始化模块
            await self._initialize_modules()
            
            # 设置信号处理
            self._setup_signal_handlers()
            
            # 设置运行标志
            self.is_running = True
            
            # 运行事件循环
            await self._run_event_loop()
            
        except Exception as e:
            self.logger.error(f"系统启动失败: {e}")
            self.logger.error(traceback.format_exc())
            await self.stop()
            
    async def stop(self):
        """异步停止系统"""
        self.logger.info("正在停止异步系统...")
        
        # 设置关闭标志
        self.shutdown_requested = True
        self.is_running = False
        
        try:
            # 停止端口管理器
            if self.port_manager:
                await self.port_manager.stop()
                
            # 停止队列管理器
            if self.queue_manager:
                await self.queue_manager.shutdown()
                
            # 停止图片管理器（新增）
            if self.image_manager:
                await self.image_manager.shutdown()
                
            # 保存上下文
            if self.context_manager:
                await self.context_manager.shutdown()
                
            self.logger.info("异步系统已安全停止")
            
        except Exception as e:
            self.logger.error(f"系统停止过程中发生错误: {e}")
            
    def _setup_signal_handlers(self):
        """设置信号处理器"""
        try:
            if sys.platform != "win32":
                # Unix信号处理
                signal.signal(signal.SIGINT, self._signal_handler)
                signal.signal(signal.SIGTERM, self._signal_handler)
                
                self.logger.info("Unix信号处理器已设置")
        except Exception as e:
            self.logger.warning(f"设置信号处理器失败: {e}")
            
    def _signal_handler(self, signum, frame):
        """信号处理函数"""
        signame = signal.Signals(signum).name
        self.logger.info(f"收到信号: {signame}")
        
        # 在主线程中安排关闭任务
        asyncio.create_task(self.stop())


async def main():
    """主函数"""
    agent = AgentCore()
    
    try:
        await agent.start()
    except KeyboardInterrupt:
        print("\n收到中断信号，正在关闭...")
    except Exception as e:
        print(f"系统异常: {e}")
        traceback.print_exc()
    finally:
        await agent.stop()


if __name__ == "__main__":
    # 运行主函数
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序已终止")
    except Exception as e:
        print(f"程序异常退出: {e}")
        sys.exit(1)