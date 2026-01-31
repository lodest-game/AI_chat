# queue_manager.py
#!/usr/bin/env python3

import asyncio
import logging
import time
from collections import defaultdict

class QueueTask:
    def __init__(self, task_id, chat_id, task_data, workflow_type, priority=0):
        self.task_id = task_id
        self.chat_id = chat_id
        self.task_data = task_data
        self.workflow_type = workflow_type
        self.created_at = time.time()
        self.priority = priority

class QueueManager:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.message_queues = {}
        self.llm_queues = {}
        self.message_consumers = {}
        self.llm_consumers = {}
        self.queue_status = {}
        self.task_callback = None
        self.message_callback = None
        self.is_running = False
        self.lock = asyncio.Lock()
        self.task_counter = 0
        
    async def initialize(self, config):
        self.is_running = True
        
    def set_task_callback(self, callback):
        self.task_callback = callback
        
    def set_message_callback(self, callback):
        self.message_callback = callback
        
    async def _get_next_task_id(self):
        async with self.lock:
            self.task_counter += 1
            return f"task_{self.task_counter}_{int(time.time())}"
        
    async def enqueue_message(self, chat_id, task_data):
        if not self.is_running:
            return None
            
        if not await self._validate_task_data(task_data, "message"):
            return None
            
        task_id = await self._get_next_task_id()
        workflow_type = await self._determine_workflow_type(task_data)
        
        task = QueueTask(
            task_id=task_id,
            chat_id=chat_id,
            task_data=task_data,
            workflow_type=workflow_type
        )
        
        if chat_id not in self.message_queues:
            self.message_queues[chat_id] = asyncio.Queue(maxsize=1000)
            await self._start_message_consumer(chat_id)
        
        try:
            await self.message_queues[chat_id].put(task)
            return task_id
        except asyncio.QueueFull:
            return None
            
    async def enqueue_llm(self, chat_id, task_data):
        if not self.is_running:
            return None
            
        if not await self._validate_task_data(task_data, "llm"):
            return None
            
        task_id = await self._get_next_task_id()
        
        task = QueueTask(
            task_id=task_id,
            chat_id=chat_id,
            task_data=task_data,
            workflow_type="C"
        )
        
        if chat_id not in self.llm_queues:
            self.llm_queues[chat_id] = asyncio.Queue(maxsize=1000)
            await self._start_llm_consumer(chat_id)
        
        try:
            await self.llm_queues[chat_id].put(task)
            return task_id
        except asyncio.QueueFull:
            return None
            
    async def _validate_task_data(self, task_data, queue_type):
        if not task_data:
            return False
            
        required_fields = ["chat_id"]
        for field in required_fields:
            if field not in task_data:
                return False
                
        if queue_type == "message" and "is_respond" not in task_data:
            return False
                
        return True
        
    async def _determine_workflow_type(self, task_data):
        return "B" if task_data.get("is_respond") == True else "A"
            
    async def _start_message_consumer(self, chat_id):
        if chat_id in self.message_consumers:
            return
            
        consumer_task = asyncio.create_task(self._message_consumer_loop(chat_id))
        self.message_consumers[chat_id] = consumer_task
        
    async def _start_llm_consumer(self, chat_id):
        if chat_id in self.llm_consumers:
            return
            
        consumer_task = asyncio.create_task(self._llm_consumer_loop(chat_id))
        self.llm_consumers[chat_id] = consumer_task
        
    async def _message_consumer_loop(self, chat_id):
        queue = self.message_queues.get(chat_id)
        if not queue:
            return
            
        while self.is_running:
            try:
                try:
                    task = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                    
                if not task:
                    continue
                    
                await self._process_message_task(task)
                queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"消息队列消费者异常 {chat_id}: {e}")
                await asyncio.sleep(1)
                
    async def _llm_consumer_loop(self, chat_id):
        queue = self.llm_queues.get(chat_id)
        if not queue:
            return
            
        while self.is_running:
            try:
                try:
                    task = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                    
                if not task:
                    continue
                    
                await self._process_llm_task(task)
                queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"LLM队列消费者异常 {chat_id}: {e}")
                await asyncio.sleep(1)
        
    async def _process_message_task(self, task):
        try:
            if self.task_callback:
                result = await self.task_callback({
                    "task_id": task.task_id,
                    "chat_id": task.chat_id,
                    "task_data": task.task_data,
                    "workflow_type": task.workflow_type,
                    "queue_type": "message"
                })
                
                if self.message_callback and result:
                    await self.message_callback(result)
                
        except Exception as e:
            self.logger.error(f"消息任务处理失败: {task.task_id}, 错误: {e}")
            
    async def _process_llm_task(self, task):
        try:
            if self.task_callback:
                result = await self.task_callback({
                    "task_id": task.task_id,
                    "chat_id": task.chat_id,
                    "task_data": task.task_data,
                    "workflow_type": task.workflow_type,
                    "queue_type": "llm"
                })
                
                if self.message_callback and result:
                    await self.message_callback(result)
                
        except Exception as e:
            self.logger.error(f"LLM任务处理失败: {task.task_id}, 错误: {e}")
            
    async def get_queue_status(self, queue_type=None, chat_id=None):
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
        
    async def clear_queue(self, queue_type, chat_id=None):
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
        pass
        
    async def shutdown(self):
        self.is_running = False
        
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
                
        await self.clear_queue("message")
        await self.clear_queue("llm")