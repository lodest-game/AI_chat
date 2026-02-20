import asyncio
import logging

class RulesManager:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.queue_manager = None
        self.task_manager = None
        self.config = None
        self.parallel_mode = "wait"
        self.is_running = False
        self.active_tasks = set()
        
    async def initialize(self, config, **kwargs):
        self.config = config
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                
        self.parallel_mode = config.get("system", {}).get("rules_manager", {}).get("mode", "wait")
        self.is_running = True
        
    async def handle_workflow_b_result(self, workflow_result):
        if not self.is_running:
            return
            
        if not workflow_result.get("success"):
            return
            
        chat_id = workflow_result.get("chat_id")
        session_id = workflow_result.get("session_id")
        
        if not chat_id or not session_id:
            return
            
        if self.parallel_mode == "all":
            await self._handle_all_mode(workflow_result, chat_id, session_id)
        elif self.parallel_mode == "wait":
            await self._handle_wait_mode(workflow_result, chat_id, session_id)
            
    async def _handle_all_mode(self, workflow_result, chat_id, session_id):
        try:
            task_data = {
                "chat_id": chat_id,
                "session_id": session_id,
                "context_data": workflow_result.get("context_data"),
                "source": "rules_manager",
                "workflow_type": "C"
            }
            
            task = asyncio.create_task(self._execute_workflow_c_direct(task_data))
            self.active_tasks.add(task)
            task.add_done_callback(
                lambda t: self.active_tasks.discard(t) if t in self.active_tasks else None
            )
            
        except Exception as e:
            self.logger.error(f"all模式处理失败: {e}")
            
    async def _handle_wait_mode(self, workflow_result, chat_id, session_id):
        try:
            task_data = {
                "chat_id": chat_id,
                "session_id": session_id,
                "context_data": workflow_result.get("context_data"),
                "source": "rules_manager",
                "workflow_type": "C"
            }
            
            task_id = await self.queue_manager.enqueue_llm(chat_id, task_data)
            
            if task_id:
                self.logger.debug(f"工作流C已加入LLM队列: task_id={task_id}, chat_id={chat_id}")
                
        except Exception as e:
            self.logger.error(f"wait模式处理失败: {e}")
            
    async def _execute_workflow_c_direct(self, task_data):
        if not self.task_manager:
            self.logger.error("task_manager未初始化")
            return
            
        try:
            task_info = {
                "task_id": f"direct_{task_data.get('chat_id')}_{task_data.get('session_id')}_{int(asyncio.get_event_loop().time())}",
                "workflow_type": "C",
                "task_data": task_data
            }
            
            result = await self.task_manager.execute_task(task_info)
            
            if result.get("success") and "response" in result:
                if self.result_callback:
                    await self.result_callback(result)
            else:
                self.logger.error(f"直接执行工作流C失败: {result.get('error')}")
                
        except Exception as e:
            self.logger.error(f"直接执行工作流C异常: {e}")
            
    def set_result_callback(self, callback):
        self.result_callback = callback
        
    def get_mode(self):
        return self.parallel_mode
        
    def set_mode(self, mode):
        if mode not in ["all", "wait"]:
            return
            
        self.parallel_mode = mode
        
    async def get_status(self):
        return {
            "parallel_mode": self.parallel_mode,
            "is_running": self.is_running,
            "active_tasks": len(self.active_tasks)
        }
        
    async def shutdown(self):
        self.is_running = False
        
        for task in self.active_tasks:
            task.cancel()
            
        if self.active_tasks:
            await asyncio.gather(*self.active_tasks, return_exceptions=True)