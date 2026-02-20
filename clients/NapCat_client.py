# NapCat_client.py
#!/usr/bin/env python3

import asyncio
import json
import logging
import re
import time
import aiohttp
import websockets
import random
import html
from pathlib import Path

class Client:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        self.config = {}
        self.is_connected = False
        self.ws_connection = None
        self.http_session = None
        self.message_callback = None
        self.bot_qq_numbers = []
        
        self.base_commands = [
            "模型列表", "模型查询", "模型更换", 
            "工具支持", "提示词", "设定提示词", "删除提示词",
            "上下文清理", "删除上下文", "重载", "热重载", "帮助"
        ]
        
        self.is_running = False
        self.receive_task = None
        self.event_queue = asyncio.Queue()
        self.send_queues = {}
        self.send_consumers = {}
        
    def _is_base_command(self, content):
        if not content or not isinstance(content, str):
            return False
        
        content = content.strip()
        
        if not content.startswith('#'):
            return False
        
        command_parts = content[1:].split()
        if not command_parts:
            return False
        
        command = command_parts[0]
        return command in self.base_commands
        
    async def start(self, config, message_callback):
        self.config = config
        self.message_callback = message_callback
        
        if not config:
            self.config = await self._load_or_create_config_async()
        
        self.bot_qq_numbers = self._parse_bot_qq_numbers(
            self.config.get("response", {}).get("bot_qq_numbers", "")
        )
        
        self.http_session = aiohttp.ClientSession()
        
        connection_config = self.config.get("connection", {})
        
        if connection_config.get("use_websocket", True):
            await self._start_websocket_connection()
        else:
            await self._start_http_server()
            
        self.is_running = True
        self.receive_task = asyncio.create_task(self._process_events())
        
        self.logger.info("异步NapCat客户端已启动")
        
    async def _load_or_create_config_async(self):
        config_path = Path(__file__).with_suffix('.json')
        
        if config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                self.logger.info(f"从文件加载配置: {config_path}")
                return config
            except Exception as e:
                self.logger.error(f"加载配置文件失败: {e}")
                return await self._create_default_config_async()
        else:
            self.logger.info(f"配置文件不存在，创建默认配置: {config_path}")
            return await self._create_default_config_async()
            
    async def _create_default_config_async(self):
        default_config = {
            "connection": {
                "ws_url": "ws://127.0.0.1:8080",
                "use_websocket": True,
                "api_url": "http://127.0.0.1:8080",
                "reconnect_interval": 5,
                "max_reconnect_attempts": 3
            },
            "response": {
                "bot_qq_numbers": "",
                "respond_to_all": False,
                "respond_to_all_probability": 0.1
            },
            "media": {
                "max_file_size": 10485760,
                "supported_formats": [".jpg", ".jpeg", ".png", ".gif", ".bmp"]
            }
        }
        
        config_path = Path(__file__).with_suffix('.json')
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            self.logger.info(f"默认配置文件已创建: {config_path}")
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
            
        return default_config
        
    async def _start_websocket_connection(self):
        connection_config = self.config.get("connection", {})
        ws_url = connection_config.get("ws_url", "ws://127.0.0.1:8080")
        max_reconnect_attempts = connection_config.get("max_reconnect_attempts", 3)
        reconnect_interval = connection_config.get("reconnect_interval", 5)
        
        attempt = 0
        while attempt < max_reconnect_attempts and not self.is_connected:
            try:
                attempt += 1
                self.logger.info(f"尝试连接WebSocket ({attempt}/{max_reconnect_attempts}): {ws_url}")
                
                self.ws_connection = await websockets.connect(ws_url)
                self.is_connected = True
                
                self.logger.info(f"WebSocket连接成功: {ws_url}")
                
                asyncio.create_task(self._receive_websocket_messages())
                
                break
                
            except Exception as e:
                self.logger.error(f"WebSocket连接失败 ({attempt}/{max_reconnect_attempts}): {e}")
                
                if attempt < max_reconnect_attempts:
                    await asyncio.sleep(reconnect_interval)
                else:
                    self.logger.error(f"WebSocket连接失败，已达最大重试次数: {ws_url}")
                    
    async def _start_http_server(self):
        self.logger.warning("HTTP服务器模式需要额外实现，当前仅支持WebSocket客户端模式")
        self.is_connected = True
        
    async def _receive_websocket_messages(self):
        try:
            async for message in self.ws_connection:
                try:
                    event_data = json.loads(message)
                    await self.event_queue.put(event_data)
                except json.JSONDecodeError as e:
                    self.logger.error(f"解析WebSocket消息失败: {e}, 消息: {message[:100]}")
                    
        except websockets.exceptions.ConnectionClosed:
            self.logger.warning("WebSocket连接已关闭")
            self.is_connected = False
        except Exception as e:
            self.logger.error(f"接收WebSocket消息异常: {e}")
            self.is_connected = False
            
    async def _process_events(self):
        while self.is_running:
            try:
                event_data = await self.event_queue.get()
                await self._handle_event(event_data)
                self.event_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"处理事件异常: {e}")
                
    async def _handle_event(self, event_data):
        try:
            post_type = event_data.get("post_type")
            
            if post_type == "message":
                await self._handle_message_event(event_data)
            elif post_type == "notice":
                await self._handle_notice_event(event_data)
            elif post_type == "request":
                await self._handle_request_event(event_data)
            elif post_type == "meta_event":
                await self._handle_meta_event(event_data)
            else:
                self.logger.debug(f"忽略未知事件类型: {post_type}")
                
        except Exception as e:
            self.logger.error(f"处理事件失败: {e}")
            
    async def _handle_message_event(self, event_data):
        message_type = event_data.get("message_type")
        
        if message_type == "private":
            await self._handle_private_message(event_data)
        elif message_type == "group":
            await self._handle_group_message(event_data)
        else:
            self.logger.debug(f"忽略未知消息类型: {message_type}")
            
    async def _handle_private_message(self, event_data):
        try:
            user_id = event_data.get("user_id")
            message = event_data.get("message", [])
            raw_message = event_data.get("raw_message", "")
            message_format = event_data.get("message_format", "array")
            
            sender = event_data.get("sender", {})
            user_nickname = sender.get("nickname", f"发言人{user_id}")
            
            chat_id = f"qq_private_{user_id}"
            
            extracted_messages = self._extract_messages(message, raw_message, message_format, user_nickname)
            
            is_respond = True
            
            message_data = {
                "chat_id": chat_id,
                "content": extracted_messages,
                "is_respond": is_respond,
                "timestamp": time.time()
            }
            
            if self.message_callback:
                if asyncio.iscoroutinefunction(self.message_callback):
                    await self.message_callback(message_data)
                else:
                    await asyncio.get_event_loop().run_in_executor(None, lambda: self.message_callback(message_data))
            else:
                self.logger.warning(f"消息回调未设置，无法处理消息: {chat_id}")
                
            self.logger.debug(f"私聊消息已处理: {user_id} -> {chat_id}")
            
        except Exception as e:
            self.logger.error(f"处理私聊消息失败: {e}")
            
    async def _handle_group_message(self, event_data):
        try:
            user_id = event_data.get("user_id")
            group_id = event_data.get("group_id")
            message = event_data.get("message", [])
            raw_message = event_data.get("raw_message", "")
            message_format = event_data.get("message_format", "array")
            
            sender = event_data.get("sender", {})
            user_nickname = sender.get("nickname", f"发言人{user_id}")
            user_card = sender.get("card", "")
            display_name = user_card if user_card and user_card.strip() else user_nickname
            
            chat_id = f"qq_group_{group_id}"
            
            extracted_messages, contains_at_bot = self._extract_group_messages(
                message, raw_message, message_format, display_name
            )
            
            is_respond = self._should_respond_group(
                extracted_messages, contains_at_bot, user_id, group_id
            )
            
            message_data = {
                "chat_id": chat_id,
                "content": extracted_messages,
                "is_respond": is_respond,
                "timestamp": time.time()
            }
            
            if self.message_callback:
                if asyncio.iscoroutinefunction(self.message_callback):
                    await self.message_callback(message_data)
                else:
                    await asyncio.get_event_loop().run_in_executor(None, lambda: self.message_callback(message_data))
            else:
                self.logger.warning(f"消息回调未设置，无法处理消息: {chat_id}")
                
            self.logger.debug(f"群聊消息已处理: {group_id}/{user_id} -> {chat_id}, respond={is_respond}")
            
        except Exception as e:
            self.logger.error(f"处理群聊消息失败: {e}")
            
    def _extract_messages(self, message, raw_message, message_format, display_name=None):
        extracted_content = []
        
        if message_format == "string" or isinstance(message, str):
            text_content = raw_message
            
            clean_text = self._remove_cq_codes(text_content)
            
            if self._is_base_command(clean_text):
                extracted_content.append({
                    "type": "text",
                    "text": clean_text
                })
                return extracted_content
            
            image_urls = self._extract_image_urls_from_text(text_content)
            
            if image_urls:
                if clean_text.strip():
                    if display_name:
                        formatted_text = f"发言人：{display_name}。\n发言内容：{clean_text}"
                    else:
                        formatted_text = clean_text
                    
                    extracted_content.append({
                        "type": "text",
                        "text": formatted_text
                    })
                
                for url in image_urls:
                    decoded_url = html.unescape(url)
                    extracted_content.append({
                        "type": "image_url",
                        "image_url": {"url": decoded_url}
                    })
            else:
                if clean_text.strip():
                    if display_name:
                        formatted_text = f"发言人：{display_name}。\n发言内容：{clean_text}"
                    else:
                        formatted_text = clean_text
                    
                    extracted_content.append({
                        "type": "text",
                        "text": formatted_text
                    })
                    
            return extracted_content
            
        if not isinstance(message, list):
            return self._extract_messages(message, raw_message, "string", display_name)
            
        text_parts = []
        image_urls = []
        
        for segment in message:
            if not isinstance(segment, dict):
                continue
                
            segment_type = segment.get("type")
            segment_data = segment.get("data", {})
            
            if segment_type == "text":
                text = segment_data.get("text", "")
                if text.strip():
                    text_parts.append(text)
                    
            elif segment_type == "image":
                file_url = segment_data.get("url", "")
                if file_url:
                    decoded_url = html.unescape(file_url)
                    image_urls.append(decoded_url)
                    
            elif segment_type == "at":
                pass
                
            elif segment_type == "face":
                face_id = segment_data.get("id", "")
                text_parts.append(f"[表情:{face_id}]")
                
            elif segment_type == "reply":
                reply_id = segment_data.get("id", "")
                text_parts.append(f"[回复:{reply_id}]")
                
            else:
                text_parts.append(f"[{segment_type}]")
        
        combined_text = " ".join(text_parts).strip()
        
        if self._is_base_command(combined_text):
            extracted_content.append({
                "type": "text",
                "text": combined_text
            })
            return extracted_content
        
        if combined_text:
            if display_name:
                formatted_text = f"发言人：{display_name}。\n发言内容：{combined_text}"
            else:
                formatted_text = combined_text
            
            extracted_content.append({
                "type": "text",
                "text": formatted_text
            })
        
        for url in image_urls:
            extracted_content.append({
                "type": "image_url",
                "image_url": {"url": url}
            })
        
        if not extracted_content:
            if display_name:
                formatted_text = f"发言人：{display_name}。\n发言内容：[消息]"
            else:
                formatted_text = "[消息]"
            
            extracted_content.append({
                "type": "text",
                "text": formatted_text
            })
        
        return extracted_content
        
    def _extract_group_messages(self, message, raw_message, message_format, display_name):
        extracted_content = []
        contains_at_bot = False
        
        if message_format == "string" or isinstance(message, str):
            if self._contains_at_bot_in_text(raw_message):
                contains_at_bot = True
                
            for qq in self.bot_qq_numbers:
                if qq:
                    raw_message = raw_message.replace(f"[CQ:at,qq={qq}]", "")
            
            raw_message = re.sub(r'\[CQ:at,qq=\d+\]', '', raw_message)
            
            content = self._extract_messages(raw_message, raw_message, "string", display_name)
            return content, contains_at_bot
            
        if not isinstance(message, list):
            content = self._extract_messages(message, raw_message, message_format, display_name)
            return content, False
            
        text_parts = []
        image_urls = []
        
        for segment in message:
            if not isinstance(segment, dict):
                continue
                
            segment_type = segment.get("type")
            segment_data = segment.get("data", {})
            
            if segment_type == "text":
                text = segment_data.get("text", "")
                if text.strip():
                    text_parts.append(text)
                    
            elif segment_type == "image":
                file_url = segment_data.get("url", "")
                if file_url and self._is_valid_media_file(file_url):
                    decoded_url = html.unescape(file_url)
                    image_urls.append(decoded_url)
                    
            elif segment_type == "at":
                qq = segment_data.get("qq", "")
                if str(qq) in self.bot_qq_numbers:
                    contains_at_bot = True
                
            elif segment_type == "face":
                face_id = segment_data.get("id", "")
                text_parts.append(f"[表情:{face_id}]")
                
            elif segment_type == "reply":
                reply_id = segment_data.get("id", "")
                text_parts.append(f"[回复:{reply_id}]")
                
            else:
                text_parts.append(f"[{segment_type}]")
        
        combined_text = " ".join(text_parts).strip()
        
        if self._is_base_command(combined_text):
            extracted_content.append({
                "type": "text",
                "text": combined_text
            })
            return extracted_content, contains_at_bot
        
        if combined_text:
            formatted_text = f"发言人：{display_name}。\n发言内容：{combined_text}"
            
            extracted_content.append({
                "type": "text",
                "text": formatted_text
            })
        
        for url in image_urls:
            extracted_content.append({
                "type": "image_url",
                "image_url": {"url": url}
            })
        
        if not extracted_content:
            formatted_text = f"发言人：{display_name}。\n发言内容：[消息]"
            
            extracted_content.append({
                "type": "text",
                "text": formatted_text
            })
        
        return extracted_content, contains_at_bot
        
    def _extract_image_urls_from_text(self, text):
        pattern = r'\[CQ:image[^\]]*?url=([^,\]]+)'
        matches = re.findall(pattern, text)
        
        decoded_matches = []
        for url in matches:
            try:
                decoded_url = html.unescape(url)
                decoded_matches.append(decoded_url)
            except Exception:
                decoded_matches.append(url)
        
        return decoded_matches
        
    def _remove_cq_codes(self, text):
        cleaned = re.sub(r'\[CQ:[^\]]+\]', '', text)
        return cleaned.strip()
        
    def _contains_at_bot_in_text(self, text):
        for qq in self.bot_qq_numbers:
            if qq and f"[CQ:at,qq={qq}]" in text:
                return True
        return False
        
    def _is_valid_media_file(self, file_url):
        try:
            supported_formats = self.config.get("media", {}).get("supported_formats", 
                [".jpg", ".jpeg", ".png", ".gif", ".bmp"])
            
            for fmt in supported_formats:
                if file_url.lower().endswith(fmt):
                    return True
                    
            return False
            
        except Exception:
            return False
            
    def _should_respond_group(self, extracted_messages, contains_at_bot, user_id, group_id):
        response_config = self.config.get("response", {})
        respond_to_all = response_config.get("respond_to_all", False)
        respond_to_all_probability = response_config.get("respond_to_all_probability", 0.1)
        
        if contains_at_bot:
            return True
            
        if extracted_messages and len(extracted_messages) == 1:
            first_item = extracted_messages[0]
            if first_item.get("type") == "text":
                text_content = first_item.get("text", "")
                if self._is_base_command(text_content):
                    return True
            
        if respond_to_all:
            return random.random() < respond_to_all_probability
            
        return False
        
    def _parse_bot_qq_numbers(self, qq_numbers_str):
        if not qq_numbers_str:
            return []
            
        numbers = re.split(r'[,，\s]+', qq_numbers_str)
        return [num.strip() for num in numbers if num.strip()]
        
    async def _handle_notice_event(self, event_data):
        notice_type = event_data.get("notice_type")
        self.logger.debug(f"收到通知事件: {notice_type}")
        
    async def _handle_request_event(self, event_data):
        request_type = event_data.get("request_type")
        self.logger.debug(f"收到请求事件: {request_type}")
        
    async def _handle_meta_event(self, event_data):
        meta_event_type = event_data.get("meta_event_type")
        if meta_event_type == "heartbeat":
            pass
            
    async def send_message_async(self, response_data):
        try:
            chat_id = response_data.get("chat_id")
            content = response_data.get("content", "")
            
            if not chat_id or not content:
                self.logger.warning("发送消息失败: 缺少chat_id或content")
                return
                
            if chat_id not in self.send_queues:
                self.send_queues[chat_id] = asyncio.Queue(maxsize=100)
                self.send_consumers[chat_id] = asyncio.create_task(
                    self._send_consumer_loop(chat_id)
                )
            
            await self.send_queues[chat_id].put(response_data)
            self.logger.debug(f"消息已加入发送队列: {chat_id}")
            
        except Exception as e:
            self.logger.error(f"发送消息失败: {e}")
            
    async def _send_consumer_loop(self, chat_id):
        queue = self.send_queues.get(chat_id)
        if not queue:
            return
            
        self.logger.debug(f"启动发送消费者: {chat_id}")
        
        while self.is_running:
            try:
                response_data = await queue.get()
                await self._send_message_direct(response_data)
                queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"发送消费者异常 {chat_id}: {e}")
                await asyncio.sleep(1)
                
        self.logger.debug(f"发送消费者停止: {chat_id}")
        
    async def _send_message_direct(self, response_data):
        try:
            chat_id = response_data.get("chat_id")
            
            target_type, target_id = self._parse_chat_id(chat_id)
            
            if not target_type or not target_id:
                self.logger.warning(f"解析chat_id失败: {chat_id}")
                return
                
            message_segments = self._convert_to_onebot_format(response_data.get("content", ""))
            
            if target_type == "private":
                await self._send_private_message_async(target_id, message_segments)
                
            elif target_type == "group":
                await self._send_group_message_async(target_id, message_segments)
                
        except Exception as e:
            self.logger.error(f"发送消息失败: {e}")
            
    def _parse_chat_id(self, chat_id):
        parts = chat_id.split('_')
        
        if len(parts) >= 3:
            platform = parts[0]
            target_type = parts[1]
            target_id = parts[2]
            
            return target_type, target_id
            
        return None, None
        
    def _convert_to_onebot_format(self, content):
        message_segments = []
        
        if isinstance(content, str):
            if content:
                message_segments.append({
                    "type": "text",
                    "data": {"text": content}
                })
                
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    
                    if item_type == "text":
                        text = item.get("text", "")
                        if text:
                            message_segments.append({
                                "type": "text",
                                "data": {"text": text}
                            })
                            
                    elif item_type == "image_url":
                        image_url = item.get("image_url", {}).get("url", "")
                        if image_url:
                            message_segments.append({
                                "type": "image",
                                "data": {"file": image_url, "url": image_url}
                            })
                            
        return message_segments
        
    async def _send_private_message_async(self, user_id, message):
        try:
            if not self.ws_connection or not self.is_connected:
                self.logger.error(f"WebSocket连接未建立，无法发送私聊消息到: {user_id}")
                return
                
            api_request = {
                "action": "send_private_msg",
                "params": {
                    "user_id": int(user_id),
                    "message": message,
                    "auto_escape": False
                },
                "echo": f"private_{user_id}_{int(time.time())}"
            }
            
            await self.ws_connection.send(json.dumps(api_request))
            self.logger.info(f"私聊消息已通过WebSocket发送: {user_id}")
            
        except Exception as e:
            self.logger.error(f"通过WebSocket发送私聊消息异常: {e}")
            
            try:
                api_url = self.config.get("connection", {}).get("api_url", "http://127.0.0.1:8080")
                endpoint = f"{api_url}/send_private_msg"
                
                payload = {
                    "user_id": int(user_id),
                    "message": message,
                    "auto_escape": False
                }
                
                async with self.http_session.post(endpoint, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get("status") == "ok":
                            self.logger.info(f"私聊消息通过HTTP发送成功: {user_id}")
                        else:
                            self.logger.error(f"私聊消息发送失败: {result}")
                    else:
                        self.logger.error(f"私聊消息HTTP错误: {response.status}")
            except Exception as http_e:
                self.logger.error(f"HTTP备用发送也失败: {http_e}")
            
    async def _send_group_message_async(self, group_id, message):
        try:
            if not self.ws_connection or not self.is_connected:
                self.logger.error(f"WebSocket连接未建立，无法发送群聊消息到: {group_id}")
                return
                
            api_request = {
                "action": "send_group_msg",
                "params": {
                    "group_id": int(group_id),
                    "message": message,
                    "auto_escape": False
                },
                "echo": f"group_{group_id}_{int(time.time())}"
            }
            
            await self.ws_connection.send(json.dumps(api_request))
            self.logger.info(f"群聊消息已通过WebSocket发送: {group_id}")
            
        except Exception as e:
            self.logger.error(f"通过WebSocket发送群聊消息异常: {e}")
            
            try:
                api_url = self.config.get("connection", {}).get("api_url", "http://127.0.0.1:8080")
                endpoint = f"{api_url}/send_group_msg"
                
                payload = {
                    "group_id": int(group_id),
                    "message": message,
                    "auto_escape": False
                }
                
                async with self.http_session.post(endpoint, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get("status") == "ok":
                            self.logger.info(f"群聊消息通过HTTP发送成功: {group_id}")
                        else:
                            self.logger.error(f"群聊消息发送失败: {result}")
                    else:
                        self.logger.error(f"群聊消息HTTP错误: {response.status}")
            except Exception as http_e:
                self.logger.error(f"HTTP备用发送也失败: {http_e}")
            
    async def is_connected_async(self):
        return self.is_connected and self.is_running
        
    async def stop(self):
        self.is_running = False
        
        if self.receive_task:
            self.receive_task.cancel()
            try:
                await self.receive_task
            except asyncio.CancelledError:
                pass
                
        for chat_id, task in self.send_consumers.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
                
        if self.ws_connection:
            await self.ws_connection.close()
            
        if self.http_session:
            await self.http_session.close()
            
        self.is_connected = False
        
        self.logger.info("异步NapCat客户端已停止")