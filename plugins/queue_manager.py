#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
queue_manager.py - 异步队列管理器
基于asyncio.Queue的完全异步队列系统，支持严格顺序处理
"""

import asyncio
import logging
import time
import uuid
from typing import Dict, Any, Optional, Callable, Deque
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class QueueTask:
    """队列任务数据结构"""
    task_id: str
    chat_id: str
    task_data: Dict[str, Any]
    workflow_type: str  # A, B, C
    created_at: float
    priority: int = 0


class QueueManager:
    """异步队列管理器"""
    
    def __init__(self):
        """初始化异步队列管理器"""
        self.logger = logging.getLogger(__name__)
        
        # 异步队列系统
        self.message_queues = {}  # chat_id -> asyncio.Queue
        self.llm_queues = {}      # chat_id -> asyncio.Queue
        
        # 队列消费者任务
        self.message_consumers = {}
        self.llm_consumers = {}
        
        # 队列状态
        self.queue_status = {}
        
        # 回调函数
        self.task_callback = None
        self.message_callback = None
        
        # 运行标志
        self.is_running = False
        
        # 锁（异步）
        self.lock = asyncio.Lock()
        
        # 任务计数器
        self.task_counter = 0
        
    async def initialize(self, config: Dict[str, Any]):
        """异步初始化队列管理器"""
        self.logger.info("异步队列管理器初始化")
        self.is_running = True
        
        self.logger.info("异步队列管理器初始化完成")
        
    def set_task_callback(self, callback: Callable):
        """设置任务回调函数"""
        self.task_callback = callback
        
    def set_message_callback(self, callback: Callable):
        """设置消息回调函数"""
        self.message_callback = callback
        
    async def _get_next_task_id(self) -> str:
        """生成任务ID"""
        async with self.lock:
            self.task_counter += 1
            return f"task_{self.task_counter}_{int(time.time())}"
        
    async def enqueue_message(self, chat_id: str, task_data: Dict[str, Any]) -> str:
        """
        将任务加入消息队列
        
        Args:
            chat_id: 对话ID
            task_data: 任务数据
            
        Returns:
            任务ID
        """
        if not self.is_running:
            self.logger.warning("队列管理器未运行，无法入队")
            return None
            
        # 验证任务数据
        if not await self._validate_task_data(task_data, "message"):
            self.logger.warning(f"任务数据验证失败: {task_data}")
            return None
            
        # 创建任务
        task_id = await self._get_next_task_id()
        workflow_type = await self._determine_workflow_type(task_data)
        
        task = QueueTask(
            task_id=task_id,
            chat_id=chat_id,
            task_data=task_data,
            workflow_type=workflow_type,
            created_at=time.time()
        )
        
        # 确保队列存在
        if chat_id not in self.message_queues:
            self.message_queues[chat_id] = asyncio.Queue(maxsize=1000)
            await self._start_message_consumer(chat_id)
        
        # 入队
        try:
            await self.message_queues[chat_id].put(task)
            self.logger.debug(f"任务已加入异步消息队列: {task_id}, chat_id: {chat_id}")
            return task_id
        except asyncio.QueueFull:
            self.logger.error(f"消息队列已满: {chat_id}")
            return None
            
    async def enqueue_llm(self, chat_id: str, task_data: Dict[str, Any]) -> str:
        """
        将任务加入LLM队列
        
        Args:
            chat_id: 对话ID
            task_data: 任务数据
            
        Returns:
            任务ID
        """
        if not self.is_running:
            self.logger.warning("队列管理器未运行，无法入队")
            return None
            
        # 验证任务数据
        if not await self._validate_task_data(task_data, "llm"):
            self.logger.warning(f"任务数据验证失败: {task_data}")
            return None
            
        # 创建任务
        task_id = await self._get_next_task_id()
        
        task = QueueTask(
            task_id=task_id,
            chat_id=chat_id,
            task_data=task_data,
            workflow_type="C",  # LLM队列只处理工作流C
            created_at=time.time()
        )
        
        # 确保队列存在
        if chat_id not in self.llm_queues:
            self.llm_queues[chat_id] = asyncio.Queue(maxsize=1000)
            await self._start_llm_consumer(chat_id)
        
        # 入队
        try:
            await self.llm_queues[chat_id].put(task)
            self.logger.debug(f"任务已加入异步LLM队列: {task_id}, chat_id: {chat_id}")
            return task_id
        except asyncio.QueueFull:
            self.logger.error(f"LLM队列已满: {chat_id}")
            return None
            
    async def _validate_task_data(self, task_data: Dict[str, Any], queue_type: str) -> bool:
        """异步验证任务数据"""
        if not task_data:
            return False
            
        # 基本验证
        required_fields = ["chat_id"]
        for field in required_fields:
            if field not in task_data:
                self.logger.warning(f"任务缺少必要字段: {field}")
                return False
                
        # 队列特定验证
        if queue_type == "message":
            if "is_respond" not in task_data:
                self.logger.warning("消息队列任务缺少is_respond字段")
                return False
                
        return True
        
    async def _determine_workflow_type(self, task_data: Dict[str, Any]) -> str:
        """确定工作流类型"""
        if task_data.get("is_respond") == True:
            return "B"  # 模型响应
        else:
            return "A"  # 非模型响应
            
    async def _start_message_consumer(self, chat_id: str):
        """启动消息队列消费者"""
        if chat_id in self.message_consumers:
            return
            
        self.logger.debug(f"启动消息队列消费者: {chat_id}")
        consumer_task = asyncio.create_task(self._message_consumer_loop(chat_id))
        self.message_consumers[chat_id] = consumer_task
        
    async def _start_llm_consumer(self, chat_id: str):
        """启动LLM队列消费者"""
        if chat_id in self.llm_consumers:
            return
            
        self.logger.debug(f"启动LLM队列消费者: {chat_id}")
        consumer_task = asyncio.create_task(self._llm_consumer_loop(chat_id))
        self.llm_consumers[chat_id] = consumer_task
        
    async def _message_consumer_loop(self, chat_id: str):
        """消息队列消费者循环"""
        queue = self.message_queues.get(chat_id)
        if not queue:
            return
            
        self.logger.debug(f"消息队列消费者启动: {chat_id}")
        
        while self.is_running:
            try:
                # 从队列获取任务（非阻塞）
                try:
                    task = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                    
                if not task:
                    continue
                    
                # 处理任务
                await self._process_message_task(task)
                
                # 标记任务完成
                queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"消息队列消费者异常 {chat_id}: {e}")
                await asyncio.sleep(1)
                
        self.logger.debug(f"消息队列消费者停止: {chat_id}")
        
    async def _llm_consumer_loop(self, chat_id: str):
        """LLM队列消费者循环"""
        queue = self.llm_queues.get(chat_id)
        if not queue:
            return
            
        self.logger.debug(f"LLM队列消费者启动: {chat_id}")
        
        while self.is_running:
            try:
                # 从队列获取任务（非阻塞）
                try:
                    task = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                    
                if not task:
                    continue
                    
                # 处理任务
                await self._process_llm_task(task)
                
                # 标记任务完成
                queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"LLM队列消费者异常 {chat_id}: {e}")
                await asyncio.sleep(1)
                
        self.logger.debug(f"LLM队列消费者停止: {chat_id}")
        
    async def _process_message_task(self, task: QueueTask):
        """处理消息队列任务"""
        try:
            self.logger.debug(f"开始处理消息任务: {task.task_id}, 工作流: {task.workflow_type}")
            
            # 调用任务回调
            if self.task_callback:
                result = await self.task_callback({
                    "task_id": task.task_id,
                    "chat_id": task.chat_id,
                    "task_data": task.task_data,
                    "workflow_type": task.workflow_type,
                    "queue_type": "message"
                })
                
                # 如果有消息回调，则调用
                if self.message_callback and result:
                    await self.message_callback(result)
                
                self.logger.debug(f"消息任务处理完成: {task.task_id}")
                
        except Exception as e:
            self.logger.error(f"消息任务处理失败: {task.task_id}, 错误: {e}")
            
    async def _process_llm_task(self, task: QueueTask):
        """处理LLM队列任务"""
        try:
            self.logger.debug(f"开始处理LLM任务: {task.task_id}")
            
            # 调用任务回调
            if self.task_callback:
                result = await self.task_callback({
                    "task_id": task.task_id,
                    "chat_id": task.chat_id,
                    "task_data": task.task_data,
                    "workflow_type": task.workflow_type,
                    "queue_type": "llm"
                })
                
                # 如果有消息回调，则调用
                if self.message_callback and result:
                    await self.message_callback(result)
                
                self.logger.debug(f"LLM任务处理完成: {task.task_id}")
                
        except Exception as e:
            self.logger.error(f"LLM任务处理失败: {task.task_id}, 错误: {e}")
            
    async def get_queue_status(self, queue_type: str = None, chat_id: str = None) -> Dict[str, Any]:
        """获取队列状态"""
        status = {}
        
        if queue_type == "message" or queue_type is None:
            if chat_id:
                queue = self.message_queues.get(chat_id)
                if queue:
                    status["message"] = {
                        "queue_length": queue.qsize(),
                        "is_consumer_active": chat_id in self.message_consumers
                    }
            else:
                status["message"] = {
                    "total_chats": len(self.message_queues),
                    "total_tasks": sum(q.qsize() for q in self.message_queues.values()),
                    "active_consumers": len(self.message_consumers)
                }
                
        if queue_type == "llm" or queue_type is None:
            if chat_id:
                queue = self.llm_queues.get(chat_id)
                if queue:
                    status["llm"] = {
                        "queue_length": queue.qsize(),
                        "is_consumer_active": chat_id in self.llm_consumers
                    }
            else:
                status["llm"] = {
                    "total_chats": len(self.llm_queues),
                    "total_tasks": sum(q.qsize() for q in self.llm_queues.values()),
                    "active_consumers": len(self.llm_consumers)
                }
                
        return status
        
    async def clear_queue(self, queue_type: str, chat_id: str = None):
        """清空队列"""
        if queue_type == "message":
            if chat_id:
                queue = self.message_queues.get(chat_id)
                if queue:
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                            queue.task_done()
                        except asyncio.QueueEmpty:
                            break
            else:
                for queue in self.message_queues.values():
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                            queue.task_done()
                        except asyncio.QueueEmpty:
                            break
        elif queue_type == "llm":
            if chat_id:
                queue = self.llm_queues.get(chat_id)
                if queue:
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                            queue.task_done()
                        except asyncio.QueueEmpty:
                            break
            else:
                for queue in self.llm_queues.values():
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                            queue.task_done()
                        except asyncio.QueueEmpty:
                            break
                            
    async def start(self):
        """启动队列管理器"""
        self.logger.info("启动异步队列管理器...")
        
    async def shutdown(self):
        """关闭队列管理器"""
        self.logger.info("关闭异步队列管理器...")
        self.is_running = False
        
        # 取消所有消费者任务
        for chat_id, task in list(self.message_consumers.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
                
        for chat_id, task in list(self.llm_consumers.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
                
        # 清空队列
        await self.clear_queue("message")
        await self.clear_queue("llm")
        
        self.logger.info("异步队列管理器已关闭")