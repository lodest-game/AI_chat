#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rules_manager.py - 异步规则与工作流管理器
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable


class RulesManager:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.queue_manager = None
        self.task_manager = None
        self.config = None
        self.parallel_mode = "wait"
        self.is_running = False
        
    async def initialize(self, config: Dict[str, Any], **kwargs):
        self.config = config
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                
        self.parallel_mode = config.get("system", {}).get("rules_manager", {}).get("mode", "wait")
        self.is_running = True
        
    async def handle_workflow_b_result(self, workflow_result: Dict[str, Any]):
        if not self.is_running:
            return
            
        if not workflow_result.get("success"):
            return
            
        chat_id = workflow_result.get("chat_id")
        session_id = workflow_result.get("session_id")
        
        if not chat_id or not session_id:
            return
            
        if self.parallel_mode == "all":
            await self._handle_all_mode(workflow_result)
        elif self.parallel_mode == "wait":
            await self._handle_wait_mode(workflow_result)
            
    async def _handle_all_mode(self, workflow_result: Dict[str, Any]):
        try:
            task = asyncio.create_task(self._execute_workflow_c(workflow_result))
            task.add_done_callback(
                lambda t: asyncio.create_task(self._handle_workflow_c_result(t.result()))
            )
        except Exception as e:
            self.logger.error(f"完全并行模式处理失败: {e}")
            
    async def _handle_wait_mode(self, workflow_result: Dict[str, Any]):
        try:
            chat_id = workflow_result.get("chat_id")
            
            task_data = {
                "chat_id": chat_id,
                "session_id": workflow_result.get("session_id"),
                "context_data": workflow_result.get("context_data"),
                "source": "rules_manager",
                "workflow_type": "C"
            }
            
            task_id = await self.queue_manager.enqueue_llm(chat_id, task_data)
            
            if task_id:
                self.logger.debug(f"工作流C已加入异步LLM队列: task_id={task_id}, chat_id={chat_id}")
                
        except Exception as e:
            self.logger.error(f"局部并行模式处理失败: {e}")
            
    async def _execute_workflow_c(self, workflow_result: Dict[str, Any]) -> Dict[str, Any]:
        if not self.task_manager:
            return {"success": False, "error": "task_manager未初始化"}
            
        try:
            task_info = {
                "task_id": f"rules_c_{workflow_result.get('chat_id')}_{workflow_result.get('session_id')}",
                "workflow_type": "C",
                "task_data": {
                    "chat_id": workflow_result.get("chat_id"),
                    "session_id": workflow_result.get("session_id"),
                    "context_data": workflow_result.get("context_data")
                }
            }
            
            return await self.task_manager.execute_task(task_info)
            
        except Exception as e:
            self.logger.error(f"异步执行工作流C失败: {e}")
            return {"success": False, "error": str(e)}
            
    async def _handle_workflow_c_result(self, result: Dict[str, Any]):
        if result.get("success"):
            if hasattr(self, "result_callback") and self.result_callback:
                await self.result_callback(result)
        else:
            self.logger.error(f"工作流C执行失败: {result.get('error')}")
            
    def set_result_callback(self, callback: Callable):
        self.result_callback = callback
        
    def get_mode(self) -> str:
        return self.parallel_mode
        
    def set_mode(self, mode: str):
        if mode not in ["all", "wait"]:
            return
            
        if mode == "all":
            self.logger.warning("在异步架构中使用all模式可能导致顺序问题")
            
        self.parallel_mode = mode
        
    async def get_status(self) -> Dict[str, Any]:
        return {
            "parallel_mode": self.parallel_mode,
            "is_running": self.is_running,
            "note": "异步架构建议使用wait模式以确保顺序"
        }
        
    async def shutdown(self):
        self.is_running = False