import uuid
import logging
import asyncio
import time
import copy

class SessionData:
    def __init__(self, session_id, chat_id, data):
        self.session_id = session_id
        self.chat_id = chat_id
        self.created_at = time.time()
        self.last_updated = time.time()
        self.data = data
        self.tool_call_count = 0

class SessionManager:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.sessions = {}
        self.chat_to_sessions = {}
        self.config = None
        self.session_locks = {}
        self.cleanup_callbacks = []
        self.session_tasks = {}
        self.is_running = False
        self.session_counter = 0
        self.image_manager = None
        
    async def initialize(self, config):
        self.config = config.get("system", {}).get("session_manager", {})
        self.session_timeout_minutes = self.config.get("session_timeout_minutes", 5)
        self.max_sessions = self.config.get("max_sessions", 100)
        self.is_running = True
        
    def set_image_manager(self, image_manager):
        self.image_manager = image_manager
        
    async def register_cleanup_callback(self, callback):
        self.cleanup_callbacks.append(callback)
    
    async def _generate_session_id(self, chat_id):
        self.session_counter += 1
        timestamp = int(time.time())
        unique_id = uuid.uuid4().hex[:8]
        return f"sess_{chat_id}_{timestamp}_{self.session_counter}_{unique_id}"
            
    async def _cleanup_session(self, session_id):
        if session_id not in self.sessions:
            return
            
        try:
            for callback in self.cleanup_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(session_id)
                    else:
                        callback(session_id)
                except Exception as e:
                    self.logger.error(f"会话清理回调失败 {session_id}: {e}")
            
            if session_id in self.session_tasks:
                del self.session_tasks[session_id]
            
            session_data = self.sessions[session_id]
            chat_id = session_data.chat_id
                
            if chat_id in self.chat_to_sessions:
                if session_id in self.chat_to_sessions[chat_id]:
                    self.chat_to_sessions[chat_id].remove(session_id)
                if not self.chat_to_sessions[chat_id]:
                    del self.chat_to_sessions[chat_id]
                        
            del self.sessions[session_id]
            
            if session_id in self.session_locks:
                del self.session_locks[session_id]
                
        except Exception as e:
            self.logger.error(f"清理会话失败 {session_id}: {e}")
            
    async def create_session(self, chat_id, context_data):
        try:
            filtered_data = await self._filter_and_reorganize_context(chat_id, context_data)
            if not filtered_data:
                return {"success": False, "error": "无法处理上下文数据"}
                
            session_id = await self._generate_session_id(chat_id)
            
            session_data = SessionData(
                session_id=session_id,
                chat_id=chat_id,
                data=filtered_data
            )
            
            self.sessions[session_id] = session_data
                
            if chat_id not in self.chat_to_sessions:
                self.chat_to_sessions[chat_id] = []
            self.chat_to_sessions[chat_id].append(session_id)
                
            return {
                "success": True,
                "session_id": session_id,
                "chat_id": chat_id,
                "message": "会话创建成功"
            }
            
        except Exception as e:
            self.logger.error(f"创建会话失败 {chat_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def _filter_and_reorganize_context(self, chat_id, context_data):
        try:
            chat_mode = context_data.get("chat_mode", "LLM")
            tools_call = context_data.get("tools_call", True)
            data_section = context_data.get("data", {})
            messages = data_section.get("messages", [])
            
            filtered_messages = copy.deepcopy(messages)
            
            last_user_message_index = -1
            for i in range(len(filtered_messages) - 1, -1, -1):
                if filtered_messages[i].get("role") == "user":
                    last_user_message_index = i
                    break
            
            for i, message in enumerate(filtered_messages):
                role = message.get("role")
                content = message.get("content", "")
                
                if chat_mode == "LLM" and role == "user":
                    if isinstance(content, list):
                        text_content = []
                        for item in content:
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    text_content.append(item.get("text", ""))
                                elif item.get("type") == "image_url":
                                    continue
                        if text_content:
                            content = " ".join(text_content) if len(text_content) > 1 else text_content[0]
                        else:
                            content = ""
                    
                    if i == last_user_message_index:
                        if not isinstance(content, str):
                            content = str(content)
                        if not content.startswith("当前请求："):
                            content = f"当前请求：\n{content}\n\n注意：以上是当前需要处理的具体问题，请优先关注并回应当前请求。历史对话仅作为背景信息参考。"
                    else:
                        if not isinstance(content, str):
                            content = str(content)
                        if content.startswith("当前请求：\n"):
                            attention_pos = content.find("\n\n注意：")
                            if attention_pos != -1:
                                lines = content.split('\n')
                                if len(lines) >= 2 and lines[0] == "当前请求：":
                                    actual_content_lines = []
                                    for line in lines[1:]:
                                        if line.startswith("注意："):
                                            break
                                        if line == "" and not actual_content_lines:
                                            continue
                                        actual_content_lines.append(line)
                                    content = '\n'.join(actual_content_lines)
                    
                    message["content"] = content
                    
                elif role == "user" and isinstance(content, list):
                    if i == last_user_message_index:
                        has_prefix = False
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text = item.get("text", "")
                                if text.startswith("当前请求："):
                                    has_prefix = True
                                    break
                        
                        if not has_prefix:
                            for j, item in enumerate(content):
                                if isinstance(item, dict) and item.get("type") == "text":
                                    current_text = item.get("text", "")
                                    content[j]["text"] = f"当前请求：\n{current_text}\n\n注意：以上是当前需要处理的具体问题，请优先关注并回应当前请求。历史对话仅作为背景信息参考。"
                                    break
            
            if self.image_manager:
                processed_messages = await self._process_images_in_messages(chat_id, filtered_messages)
                filtered_messages = processed_messages
                
            filtered_data = {
                "model": data_section.get("model", "local_model"),
                "messages": filtered_messages,
                "max_tokens": data_section.get("max_tokens", 64000),
                "temperature": data_section.get("temperature", 0.7),
                "stream": data_section.get("stream", False)
            }
            
            if tools_call and "tools" in data_section:
                filtered_data["tools"] = data_section["tools"]
                
            return filtered_data
            
        except Exception as e:
            self.logger.error(f"过滤上下文数据失败 {chat_id}: {e}")
            return {}
            
    async def _process_images_in_messages(self, chat_id, messages):
        processed_messages = []
        
        for message in messages:
            if not isinstance(message, dict):
                processed_messages.append(message)
                continue
                
            role = message.get("role")
            content = message.get("content", "")
            
            if role != "user":
                processed_messages.append(message)
                continue
                
            if isinstance(content, str):
                processed_messages.append(message)
                continue
                    
            elif isinstance(content, list):
                new_content = []
                
                for item in content:
                    if isinstance(item, dict):
                        item_type = item.get("type")
                        
                        if item_type == "text":
                            new_content.append(item)
                            
                        elif item_type == "image_url":
                            image_url = item.get("image_url", {})
                            if isinstance(image_url, dict):
                                url = image_url.get("url", "")
                            elif isinstance(image_url, str):
                                url = image_url
                            else:
                                url = ""
                                
                            if url and url.startswith(("http://", "https://")):
                                result = await self._handle_image_url(chat_id, url)
                                if result:
                                    new_content.append({
                                        "type": "image_url",
                                        "image_url": {"url": result}
                                    })
                                else:
                                    self.logger.debug(f"图片URL无法处理，已移除: {url[:50]}...")
                            elif url and url.startswith("data:image/"):
                                new_content.append(item)
                            else:
                                continue
                        else:
                            new_content.append(item)
                    else:
                        new_content.append(item)
                        
                if new_content:
                    processed_message = message.copy()
                    processed_message["content"] = new_content
                    processed_messages.append(processed_message)
                else:
                    processed_messages.append(message)
            else:
                processed_messages.append(message)
                
        return processed_messages
        
    async def _handle_image_url(self, chat_id, url):
        try:
            base64_data = await self.image_manager.get_image_base64(chat_id, url)
            if base64_data:
                return base64_data
                
            is_processing = url in self.image_manager.processing_tasks
                
            if is_processing:
                try:
                    task = self.image_manager.processing_tasks.get(url)
                    if task:
                        result = await task
                        if result.get("success"):
                            base64_data = await self.image_manager.get_image_base64(chat_id, url)
                            return base64_data
                except Exception as e:
                    self.logger.error(f"等待图片处理失败: {e}")
                    
            return None
            
        except Exception as e:
            self.logger.error(f"处理图片URL失败: {e}")
            return None
            
    async def add_tool_call_message(self, session_id, assistant_message):
        if session_id not in self.sessions:
            return {"success": False, "error": f"会话不存在: {session_id}"}
            
        try:
            session_data = self.sessions[session_id]
            session_data.data["messages"].append(assistant_message)
            session_data.last_updated = time.time()
                
            return {"success": True, "message": "工具调用消息已添加到会话"}
            
        except Exception as e:
            self.logger.error(f"添加工具调用消息失败 {session_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def add_tool_results(self, session_id, tool_results):
        if session_id not in self.sessions:
            return {"success": False, "error": f"会话不存在: {session_id}"}
            
        try:
            session_data = self.sessions[session_id]
                
            for tool_result in tool_results:
                if isinstance(tool_result, dict):
                    session_data.data["messages"].append(tool_result)
                    
            session_data.tool_call_count += len(tool_results)
            session_data.last_updated = time.time()
                
            return {
                "success": True,
                "message": f"工具结果已添加到会话，新增 {len(tool_results)} 个结果",
                "tool_call_count": session_data.tool_call_count
            }
            
        except Exception as e:
            self.logger.error(f"添加工具结果失败 {session_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def get_session(self, session_id):
        if session_id not in self.sessions:
            return {"success": False, "error": f"会话不存在: {session_id}"}
            
        try:
            if session_id not in self.session_locks:
                self.session_locks[session_id] = asyncio.Lock()
                
            async with self.session_locks[session_id]:
                session_data = self.sessions[session_id]
                session_data.last_updated = time.time()
                
                return {
                    "success": True,
                    "data": session_data.data,
                    "session_id": session_id,
                    "chat_id": session_data.chat_id,
                    "tool_call_count": session_data.tool_call_count
                }
                
        except Exception as e:
            self.logger.error(f"获取会话数据失败 {session_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def update_session(self, session_id, tool_results):
        return await self.add_tool_results(session_id, tool_results)
            
    async def cleanup_session(self, session_id):
        if session_id not in self.sessions:
            return {"success": False, "error": f"会话不存在: {session_id}"}
            
        try:
            await self._cleanup_session(session_id)
            return {"success": True, "message": "会话已清理"}
            
        except Exception as e:
            self.logger.error(f"清理会话失败 {session_id}: {e}")
            return {"success": False, "error": str(e)}
            
    async def get_sessions_by_chat_id(self, chat_id):
        return self.chat_to_sessions.get(chat_id, [])
            
    async def get_session_info(self, session_id):
        if session_id not in self.sessions:
            return None
            
        session_data = self.sessions[session_id]
        
        return {
            "session_id": session_id,
            "chat_id": session_data.chat_id,
            "created_at": session_data.created_at,
            "last_updated": session_data.last_updated,
            "tool_call_count": session_data.tool_call_count,
            "age_seconds": time.time() - session_data.created_at,
            "inactive_seconds": time.time() - session_data.last_updated
        }
        
    async def get_all_sessions_info(self):
        sessions_info = []
        
        for session_id, session_data in self.sessions.items():
            sessions_info.append({
                "session_id": session_id,
                "chat_id": session_data.chat_id,
                "created_at": session_data.created_at,
                "last_updated": session_data.last_updated,
                "tool_call_count": session_data.tool_call_count,
                "age_seconds": time.time() - session_data.created_at,
                "inactive_seconds": time.time() - session_data.last_updated
            })
                
        return sessions_info
            
    async def get_status(self):
        return {
            "total_sessions": len(self.sessions),
            "max_sessions": self.max_sessions,
            "session_timeout_minutes": self.session_timeout_minutes,
            "sessions_by_chat": {
                chat_id: len(sessions)
                for chat_id, sessions in self.chat_to_sessions.items()
            },
            "has_image_manager": self.image_manager is not None
        }
        
    async def shutdown(self):
        self.is_running = False
        session_ids = list(self.sessions.keys())
        for session_id in session_ids:
            await self._cleanup_session(session_id)