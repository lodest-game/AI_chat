#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rules_manager.py - 异步规则与工作流管理器
基于asyncio的完全异步规则决策系统
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable


class RulesManager:
    """异步规则管理器"""
    
    def __init__(self):
        """初始化异步规则管理器"""
        self.logger = logging.getLogger(__name__)
        
        # 模块引用
        self.queue_manager = None
        self.task_manager = None
        
        # 配置
        self.config = None
        
        # 并行模式
        self.parallel_mode = "wait"  # 在异步架构中，使用wait模式确保顺序
        
        # 运行标志
        self.is_running = False
        
    async def initialize(self, config: Dict[str, Any], **kwargs):
        """异步初始化规则管理器"""
        self.config = config
        
        # 设置模块引用
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                
        # 获取并行模式配置
        self.parallel_mode = config.get("system", {}).get("rules_manager", {}).get("mode", "wait")
        
        # 在异步架构中，强制使用wait模式确保顺序
        self.parallel_mode = "wait"
        
        # 设置运行标志
        self.is_running = True
        
        self.logger.info(f"异步规则管理器初始化完成，并行模式: {self.parallel_mode}（异步架构强制使用wait模式）")
        
    async def handle_workflow_b_result(self, workflow_result: Dict[str, Any]):
        """
        异步处理工作流B的结果
        
        Args:
            workflow_result: 工作流B的执行结果
        """
        if not self.is_running:
            self.logger.warning("规则管理器未运行")
            return
            
        if not workflow_result.get("success"):
            self.logger.warning(f"工作流B执行失败: {workflow_result.get('error')}")
            return
            
        # 提取必要信息
        chat_id = workflow_result.get("chat_id")
        session_id = workflow_result.get("session_id")
        
        if not chat_id or not session_id:
            self.logger.error("工作流B结果缺少chat_id或session_id")
            return
            
        self.logger.info(f"异步处理工作流B结果: chat_id={chat_id}, session_id={session_id}")
        
        # 根据并行模式决定处理方式
        if self.parallel_mode == "all":
            await self._handle_all_mode(workflow_result)
        elif self.parallel_mode == "wait":
            await self._handle_wait_mode(workflow_result)
        else:
            self.logger.error(f"未知的并行模式: {self.parallel_mode}")
            
    async def _handle_all_mode(self, workflow_result: Dict[str, Any]):
        """完全并行模式处理（异步版本）"""
        try:
            # 创建异步任务执行工作流C
            task = asyncio.create_task(
                self._execute_workflow_c(workflow_result)
            )
            
            # 添加回调处理结果
            task.add_done_callback(
                lambda t: asyncio.create_task(self._handle_workflow_c_result(t.result()))
            )
            
            self.logger.debug(f"已创建异步任务执行工作流C: chat_id={workflow_result.get('chat_id')}")
            
        except Exception as e:
            self.logger.error(f"完全并行模式处理失败: {e}")
            
    async def _handle_wait_mode(self, workflow_result: Dict[str, Any]):
        """局部并行模式处理（异步版本）"""
        try:
            # 将任务加入LLM队列
            chat_id = workflow_result.get("chat_id")
            
            task_data = {
                "chat_id": chat_id,
                "session_id": workflow_result.get("session_id"),
                "context_data": workflow_result.get("context_data"),
                "source": "rules_manager",
                "workflow_type": "C"
            }
            
            # 异步入队
            task_id = await self.queue_manager.enqueue_llm(chat_id, task_data)
            
            if task_id:
                self.logger.debug(f"工作流C已加入异步LLM队列: task_id={task_id}, chat_id={chat_id}")
            else:
                self.logger.error(f"工作流C异步入队失败: chat_id={chat_id}")
                
        except Exception as e:
            self.logger.error(f"局部并行模式处理失败: {e}")
            
    async def _execute_workflow_c(self, workflow_result: Dict[str, Any]) -> Dict[str, Any]:
        """异步执行工作流C"""
        if not self.task_manager:
            return {
                "success": False,
                "error": "task_manager未初始化"
            }
            
        try:
            # 准备任务信息
            task_info = {
                "task_id": f"rules_c_{workflow_result.get('chat_id')}_{workflow_result.get('session_id')}",
                "workflow_type": "C",
                "task_data": {
                    "chat_id": workflow_result.get("chat_id"),
                    "session_id": workflow_result.get("session_id"),
                    "context_data": workflow_result.get("context_data")
                }
            }
            
            # 异步执行工作流C
            result = await self.task_manager.execute_task(task_info)
            
            return result
            
        except Exception as e:
            self.logger.error(f"异步执行工作流C失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }
            
    async def _handle_workflow_c_result(self, result: Dict[str, Any]):
        """异步处理工作流C的结果"""
        if result.get("success"):
            self.logger.info(f"工作流C执行成功: chat_id={result.get('chat_id')}")
            
            # 在实际实现中，应该通过回调函数通知Agent_core
            if hasattr(self, "result_callback") and self.result_callback:
                await self.result_callback(result)
        else:
            self.logger.error(f"工作流C执行失败: {result.get('error')}")
            
    def set_result_callback(self, callback: Callable):
        """设置结果回调函数"""
        self.result_callback = callback
        
    def get_mode(self) -> str:
        """获取当前并行模式"""
        return self.parallel_mode
        
    def set_mode(self, mode: str):
        """设置并行模式"""
        if mode not in ["all", "wait"]:
            self.logger.error(f"无效的并行模式: {mode}")
            return
            
        # 在异步架构中，建议使用wait模式
        if mode == "all":
            self.logger.warning("在异步架构中使用all模式可能导致顺序问题")
            
        old_mode = self.parallel_mode
        self.parallel_mode = mode
            
        self.logger.info(f"并行模式已更改: {old_mode} -> {mode}")
        
    async def get_status(self) -> Dict[str, Any]:
        """异步获取状态信息"""
        status = {
            "parallel_mode": self.parallel_mode,
            "is_running": self.is_running,
            "note": "异步架构建议使用wait模式以确保顺序"
        }
        
        return status
        
    async def shutdown(self):
        """关闭异步规则管理器"""
        self.is_running = False
        
        self.logger.info("异步规则管理器已关闭")