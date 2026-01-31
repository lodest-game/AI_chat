#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NapCat_client.py - NapCat QQ客户端实现
完全异步版本，支持单消费者队列模式
"""

import asyncio
import json
import logging
import re
import time
import urllib.parse
import aiohttp
import websockets
from typing import Dict, Any, List, Optional, Callable
from pathlib import Path
import random
import os
import html


class Client:
    """完全异步的NapCat QQ客户端"""
    
    def __init__(self):
        """初始化异步客户端"""
        self.logger = logging.getLogger(__name__)
        
        # 配置
        self.config = {}
        
        # 连接状态
        self.is_connected = False
        self.ws_connection = None
        self.http_session = None
        
        # 消息回调
        self.message_callback = None
        
        # 机器人QQ号
        self.bot_qq_numbers = []
        
        # 基础指令列表（与EssentialsManager中的命令保持一致）
        self.base_commands = [
            "模型列表", "模型查询", "模型更换", 
            "工具支持", "提示词", "设定提示词", "删除提示词",
            "上下文清理", "删除上下文", "重载", "热重载", "帮助"
        ]
        
        # 运行标志
        self.is_running = False
        
        # 任务
        self.receive_task = None
        
        # 事件队列
        self.event_queue = asyncio.Queue()
        
        # 发送队列（单消费者模式）
        self.send_queues = {}  # chat_id -> asyncio.Queue
        self.send_consumers = {}  # chat_id -> asyncio.Task
        
    # 添加方法：检查是否是基础指令
    def _is_base_command(self, content: str) -> bool:
        """检查内容是否是基础指令"""
        if not content or not isinstance(content, str):
            return False
        
        # 移除可能的空格和换行符
        content = content.strip()
        
        # 检查是否以 # 开头
        if not content.startswith('#'):
            return False
        
        # 提取命令部分（去掉 # 前缀）
        command_parts = content[1:].split()
        if not command_parts:
            return False
        
        command = command_parts[0]
        
        # 检查是否是已知的基础指令
        return command in self.base_commands
        
    async def start(self, config: Dict[str, Any], message_callback: Callable):
        """
        异步启动客户端
        
        Args:
            config: 客户端配置
            message_callback: 异步消息回调函数
        """
        self.config = config
        self.message_callback = message_callback
        
        # 如果没有传入配置，尝试加载或生成配置文件
        if not config:
            self.config = await self._load_or_create_config_async()
        
        # 解析机器人QQ号
        self.bot_qq_numbers = self._parse_bot_qq_numbers(
            self.config.get("response", {}).get("bot_qq_numbers", "")
        )
        
        # 建立HTTP会话
        self.http_session = aiohttp.ClientSession()
        
        # 根据配置选择连接方式
        connection_config = self.config.get("connection", {})
        
        if connection_config.get("use_websocket", True):
            # WebSocket连接
            await self._start_websocket_connection()
        else:
            # HTTP上报
            await self._start_http_server()
            
        self.is_running = True
        
        # 启动事件处理任务
        self.receive_task = asyncio.create_task(self._process_events())
        
        self.logger.info("异步NapCat客户端已启动")
        
    async def _load_or_create_config_async(self) -> Dict[str, Any]:
        """异步加载或创建配置文件"""
        config_path = Path(__file__).with_suffix('.json')
        
        if config_path.exists():
            try:
                loop = asyncio.get_event_loop()
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = await loop.run_in_executor(None, json.load, f)
                self.logger.info(f"从文件加载配置: {config_path}")
                return config
            except Exception as e:
                self.logger.error(f"加载配置文件失败: {e}")
                return await self._create_default_config_async()
        else:
            self.logger.info(f"配置文件不存在，创建默认配置: {config_path}")
            return await self._create_default_config_async()
            
    async def _create_default_config_async(self) -> Dict[str, Any]:
        """异步创建默认配置并保存到文件"""
        default_config = {
            "connection": {
                "ws_url": "ws://127.0.0.1:8080",
                "use_websocket": True,
                "api_url": "http://127.0.0.1:8080",
                "reconnect_interval": 5,
                "max_reconnect_attempts": 3
            },
            "response": {
                "bot_qq_numbers": "",  # 多个QQ号用逗号分隔
                "respond_to_all": False,
                "respond_to_all_probability": 0.1
            },
            "media": {
                "max_file_size": 10485760,  # 10MB
                "supported_formats": [".jpg", ".jpeg", ".png", ".gif", ".bmp"]
            }
        }
        
        # 异步保存到文件
        config_path = Path(__file__).with_suffix('.json')
        try:
            loop = asyncio.get_event_loop()
            with open(config_path, 'w', encoding='utf-8') as f:
                await loop.run_in_executor(
                    None, 
                    lambda: json.dump(default_config, f, ensure_ascii=False, indent=2)
                )
            self.logger.info(f"默认配置文件已创建: {config_path}")
        except Exception as e:
            self.logger.error(f"保存配置文件失败: {e}")
            
        return default_config
        
    async def _start_websocket_connection(self):
        """异步启动WebSocket连接"""
        connection_config = self.config.get("connection", {})
        ws_url = connection_config.get("ws_url", "ws://127.0.0.1:8080")
        max_reconnect_attempts = connection_config.get("max_reconnect_attempts", 3)
        reconnect_interval = connection_config.get("reconnect_interval", 5)
        
        attempt = 0
        while attempt < max_reconnect_attempts and not self.is_connected:
            try:
                attempt += 1
                self.logger.info(f"尝试连接WebSocket ({attempt}/{max_reconnect_attempts}): {ws_url}")
                
                # 连接WebSocket
                self.ws_connection = await websockets.connect(ws_url)
                self.is_connected = True
                
                self.logger.info(f"WebSocket连接成功: {ws_url}")
                
                # 启动接收任务
                asyncio.create_task(self._receive_websocket_messages())
                
                break
                
            except Exception as e:
                self.logger.error(f"WebSocket连接失败 ({attempt}/{max_reconnect_attempts}): {e}")
                
                if attempt < max_reconnect_attempts:
                    await asyncio.sleep(reconnect_interval)
                else:
                    self.logger.error(f"WebSocket连接失败，已达最大重试次数: {ws_url}")
                    
    async def _start_http_server(self):
        """启动HTTP服务器（接收HTTP上报）"""
        self.logger.warning("HTTP服务器模式需要额外实现，当前仅支持WebSocket客户端模式")
        self.is_connected = True
        
    async def _receive_websocket_messages(self):
        """异步接收WebSocket消息"""
        try:
            async for message in self.ws_connection:
                try:
                    # 解析JSON消息
                    event_data = json.loads(message)
                    
                    # 放入事件队列
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
        """异步处理事件"""
        while self.is_running:
            try:
                # 从队列获取事件
                event_data = await self.event_queue.get()
                
                # 处理事件
                await self._handle_event(event_data)
                
                # 标记任务完成
                self.event_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"处理事件异常: {e}")
                
    async def _handle_event(self, event_data: Dict[str, Any]):
        """异步处理事件"""
        try:
            post_type = event_data.get("post_type")
            
            if post_type == "message":
                # 消息事件
                await self._handle_message_event(event_data)
            elif post_type == "notice":
                # 通知事件
                await self._handle_notice_event(event_data)
            elif post_type == "request":
                # 请求事件
                await self._handle_request_event(event_data)
            elif post_type == "meta_event":
                # 元事件（心跳等）
                await self._handle_meta_event(event_data)
            else:
                self.logger.debug(f"忽略未知事件类型: {post_type}")
                
        except Exception as e:
            self.logger.error(f"处理事件失败: {e}")
            
    async def _handle_message_event(self, event_data: Dict[str, Any]):
        """异步处理消息事件"""
        message_type = event_data.get("message_type")
        
        if message_type == "private":
            # 私聊消息
            await self._handle_private_message(event_data)
        elif message_type == "group":
            # 群聊消息
            await self._handle_group_message(event_data)
        else:
            self.logger.debug(f"忽略未知消息类型: {message_type}")
            
    async def _handle_private_message(self, event_data: Dict[str, Any]):
        """异步处理私聊消息"""
        try:
            # 提取基本信息
            user_id = event_data.get("user_id")
            message = event_data.get("message", [])
            raw_message = event_data.get("raw_message", "")
            message_format = event_data.get("message_format", "array")
            
            # 获取发送者信息
            sender = event_data.get("sender", {})
            user_nickname = sender.get("nickname", f"发言人{user_id}")
            
            # 构建chat_id
            chat_id = f"qq_private_{user_id}"
            
            # 提取并转换消息
            extracted_messages = self._extract_messages(message, raw_message, message_format, user_nickname)
            
            # 私聊始终需要响应
            is_respond = True
            
            # 构建发送给系统的消息数据
            message_data = {
                "chat_id": chat_id,
                "content": extracted_messages,
                "is_respond": is_respond,
                "timestamp": time.time()
            }
            
            # 异步调用消息回调
            if self.message_callback:
                if asyncio.iscoroutinefunction(self.message_callback):
                    await self.message_callback(message_data)
                else:
                    # 如果是同步回调，在线程中执行
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, lambda: self.message_callback(message_data))
            else:
                self.logger.warning(f"消息回调未设置，无法处理消息: {chat_id}")
                
            self.logger.debug(f"私聊消息已处理: {user_id} -> {chat_id}")
            
        except Exception as e:
            self.logger.error(f"处理私聊消息失败: {e}")
            
    async def _handle_group_message(self, event_data: Dict[str, Any]):
        """异步处理群聊消息"""
        try:
            # 提取基本信息
            user_id = event_data.get("user_id")
            group_id = event_data.get("group_id")
            message = event_data.get("message", [])
            raw_message = event_data.get("raw_message", "")
            message_format = event_data.get("message_format", "array")
            
            # 获取发送者信息
            sender = event_data.get("sender", {})
            user_nickname = sender.get("nickname", f"发言人{user_id}")
            user_card = sender.get("card", "")  # 群名片
            display_name = user_card if user_card and user_card.strip() else user_nickname
            
            # 构建chat_id
            chat_id = f"qq_group_{group_id}"
            
            # 提取并转换消息
            extracted_messages, contains_at_bot = self._extract_group_messages(
                message, raw_message, message_format, display_name
            )
            
            # 判断是否需要响应
            is_respond = self._should_respond_group(
                extracted_messages, contains_at_bot, user_id, group_id
            )
            
            # 构建发送给系统的消息数据
            message_data = {
                "chat_id": chat_id,
                "content": extracted_messages,
                "is_respond": is_respond,
                "timestamp": time.time()
            }
            
            # 异步调用消息回调
            if self.message_callback:
                if asyncio.iscoroutinefunction(self.message_callback):
                    await self.message_callback(message_data)
                else:
                    # 如果是同步回调，在线程中执行
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, lambda: self.message_callback(message_data))
            else:
                self.logger.warning(f"消息回调未设置，无法处理消息: {chat_id}")
                
            self.logger.debug(f"群聊消息已处理: {group_id}/{user_id} -> {chat_id}, respond={is_respond}")
            
        except Exception as e:
            self.logger.error(f"处理群聊消息失败: {e}")
            
    def _extract_messages(self, message: Any, raw_message: str, message_format: str, display_name: str = None) -> List[Dict[str, Any]]:
        """提取消息内容，添加发言人身份标识"""
        extracted_content = []
        
        # 处理字符串格式的消息
        if message_format == "string" or isinstance(message, str):
            # 消息是字符串，尝试提取CQ码
            text_content = raw_message
            
            # 移除CQ码中的@信息，保留纯文本
            clean_text = self._remove_cq_codes(text_content)
            
            # 检查是否是基础指令
            if self._is_base_command(clean_text):
                # 基础指令：不添加发言人前缀，直接发送指令
                extracted_content.append({
                    "type": "text",
                    "text": clean_text
                })
                return extracted_content
            
            # 如果不是指令，正常处理
            # 如果有图片，分离文本和图片
            image_urls = self._extract_image_urls_from_text(text_content)
            
            # 如果有图片，先处理文本部分
            if image_urls:
                if clean_text.strip():
                    # 添加发言人身份标识
                    if display_name:
                        formatted_text = f"发言人：{display_name}。\n发言内容：{clean_text}"
                    else:
                        formatted_text = clean_text
                    
                    extracted_content.append({
                        "type": "text",
                        "text": formatted_text
                    })
                
                # 添加图片
                for url in image_urls:
                    decoded_url = html.unescape(url)
                    extracted_content.append({
                        "type": "image_url",
                        "image_url": {"url": decoded_url}
                    })
            else:
                # 纯文本消息
                if clean_text.strip():
                    # 添加发言人身份标识
                    if display_name:
                        formatted_text = f"发言人：{display_name}。\n发言内容：{clean_text}"
                    else:
                        formatted_text = clean_text
                    
                    extracted_content.append({
                        "type": "text",
                        "text": formatted_text
                    })
                    
            return extracted_content
            
        # 处理数组格式的消息
        if not isinstance(message, list):
            # 如果不是列表，转为字符串处理
            return self._extract_messages(message, raw_message, "string", display_name)
            
        # 收集所有文本内容
        text_parts = []
        image_urls = []
        
        # 处理每个消息段
        for segment in message:
            if not isinstance(segment, dict):
                continue
                
            segment_type = segment.get("type")
            segment_data = segment.get("data", {})
            
            if segment_type == "text":
                # 文本消息
                text = segment_data.get("text", "")
                if text.strip():
                    text_parts.append(text)
                    
            elif segment_type == "image":
                # 图片消息
                file_url = segment_data.get("url", "")
                if file_url:
                    decoded_url = html.unescape(file_url)
                    image_urls.append(decoded_url)
                    
            elif segment_type == "at":
                # @消息 - 不添加到文本中，只用于判断是否需要响应
                pass
                
            elif segment_type == "face":
                # QQ表情 - 转换为文本表示
                face_id = segment_data.get("id", "")
                text_parts.append(f"[表情:{face_id}]")
                
            elif segment_type == "reply":
                # 回复消息 - 转换为文本表示
                reply_id = segment_data.get("id", "")
                text_parts.append(f"[回复:{reply_id}]")
                
            else:
                # 其他类型消息
                text_parts.append(f"[{segment_type}]")
        
        # 合并文本内容
        combined_text = " ".join(text_parts).strip()
        
        # 检查是否是基础指令
        if self._is_base_command(combined_text):
            # 基础指令：不添加发言人前缀，直接发送指令
            extracted_content.append({
                "type": "text",
                "text": combined_text
            })
            return extracted_content
        
        # 如果不是指令，正常处理
        # 如果有文本内容
        if combined_text:
            # 添加发言人身份标识
            if display_name:
                formatted_text = f"发言人：{display_name}。\n发言内容：{combined_text}"
            else:
                formatted_text = combined_text
            
            extracted_content.append({
                "type": "text",
                "text": formatted_text
            })
        
        # 添加图片
        for url in image_urls:
            extracted_content.append({
                "type": "image_url",
                "image_url": {"url": url}
            })
        
        # 如果既没有文本也没有图片，添加一个默认消息
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
        
    def _extract_group_messages(self, message: Any, raw_message: str, message_format: str, display_name: str) -> tuple:
        """提取群聊消息内容，添加发言人身份标识"""
        extracted_content = []
        contains_at_bot = False
        
        # 处理字符串格式的消息
        if message_format == "string" or isinstance(message, str):
            # 检查是否包含@机器人
            if self._contains_at_bot_in_text(raw_message):
                contains_at_bot = True
                
            # 移除@机器人的CQ码
            for qq in self.bot_qq_numbers:
                if qq:
                    raw_message = raw_message.replace(f"[CQ:at,qq={qq}]", "")
            
            # 移除其他@的CQ码
            raw_message = re.sub(r'\[CQ:at,qq=\d+\]', '', raw_message)
            
            # 提取消息内容
            content = self._extract_messages(raw_message, raw_message, "string", display_name)
            return content, contains_at_bot
            
        # 处理数组格式的消息
        if not isinstance(message, list):
            content = self._extract_messages(message, raw_message, message_format, display_name)
            return content, False
            
        # 收集所有文本内容
        text_parts = []
        image_urls = []
        
        # 处理每个消息段
        for segment in message:
            if not isinstance(segment, dict):
                continue
                
            segment_type = segment.get("type")
            segment_data = segment.get("data", {})
            
            if segment_type == "text":
                # 文本消息
                text = segment_data.get("text", "")
                if text.strip():
                    text_parts.append(text)
                    
            elif segment_type == "image":
                # 图片消息
                file_url = segment_data.get("url", "")
                if file_url and self._is_valid_media_file(file_url):
                    decoded_url = html.unescape(file_url)
                    image_urls.append(decoded_url)
                    
            elif segment_type == "at":
                # @消息
                qq = segment_data.get("qq", "")
                if str(qq) in self.bot_qq_numbers:
                    contains_at_bot = True
                # 不将@信息添加到文本内容中
                
            elif segment_type == "face":
                # QQ表情
                face_id = segment_data.get("id", "")
                text_parts.append(f"[表情:{face_id}]")
                
            elif segment_type == "reply":
                # 回复消息
                reply_id = segment_data.get("id", "")
                text_parts.append(f"[回复:{reply_id}]")
                
            else:
                # 其他类型消息
                text_parts.append(f"[{segment_type}]")
        
        # 合并文本内容
        combined_text = " ".join(text_parts).strip()
        
        # 检查是否是基础指令
        if self._is_base_command(combined_text):
            # 基础指令：不添加发言人前缀，直接发送指令
            extracted_content.append({
                "type": "text",
                "text": combined_text
            })
            return extracted_content, contains_at_bot
        
        # 如果不是指令，正常处理
        # 如果有文本内容
        if combined_text:
            formatted_text = f"发言人：{display_name}。\n发言内容：{combined_text}"
            
            extracted_content.append({
                "type": "text",
                "text": formatted_text
            })
        
        # 添加图片
        for url in image_urls:
            extracted_content.append({
                "type": "image_url",
                "image_url": {"url": url}
            })
        
        # 如果既没有文本也没有图片，添加一个默认消息
        if not extracted_content:
            formatted_text = f"发言人：{display_name}。\n发言内容：[消息]"
            
            extracted_content.append({
                "type": "text",
                "text": formatted_text
            })
        
        return extracted_content, contains_at_bot
        
    def _extract_image_urls_from_text(self, text: str) -> List[str]:
        """从文本中提取图片URL"""
        # 更精确的正则表达式匹配CQ码中的url参数
        # 匹配格式: [CQ:image, ... ,url=URL, ...]
        pattern = r'\[CQ:image[^\]]*?url=([^,\]]+)'
        matches = re.findall(pattern, text)
        
        # 处理HTML实体编码（如&amp;转换为&）
        decoded_matches = []
        for url in matches:
            try:
                # HTML实体解码
                decoded_url = html.unescape(url)
                decoded_matches.append(decoded_url)
            except Exception:
                decoded_matches.append(url)
        
        return decoded_matches
        
    def _remove_cq_codes(self, text: str) -> str:
        """移除CQ码"""
        cleaned = re.sub(r'\[CQ:[^\]]+\]', '', text)
        return cleaned.strip()
        
    def _contains_at_bot_in_text(self, text: str) -> bool:
        """检查文本中是否包含@机器人"""
        for qq in self.bot_qq_numbers:
            if qq and f"[CQ:at,qq={qq}]" in text:
                return True
        return False
        
    def _is_valid_media_file(self, file_url: str) -> bool:
        """检查媒体文件是否有效"""
        try:
            supported_formats = self.config.get("media", {}).get("supported_formats", 
                [".jpg", ".jpeg", ".png", ".gif", ".bmp"])
            
            parsed_url = urllib.parse.urlparse(file_url)
            path = parsed_url.path.lower()
            
            for fmt in supported_formats:
                if path.endswith(fmt):
                    return True
                    
            return False
            
        except Exception:
            return False
            
    def _should_respond_group(self, extracted_messages: List[Dict[str, Any]], 
                            contains_at_bot: bool,
                            user_id: int, group_id: int) -> bool:
        """判断群聊消息是否需要响应"""
        response_config = self.config.get("response", {})
        respond_to_all = response_config.get("respond_to_all", False)
        respond_to_all_probability = response_config.get("respond_to_all_probability", 0.1)
        
        # 如果@了机器人，始终响应
        if contains_at_bot:
            return True
            
        # 检查是否是基础指令（指令消息即使没有@也应该响应）
        if extracted_messages and len(extracted_messages) == 1:
            first_item = extracted_messages[0]
            if first_item.get("type") == "text":
                text_content = first_item.get("text", "")
                if self._is_base_command(text_content):
                    return True
            
        # 全响应模式：按概率随机响应
        if respond_to_all:
            return random.random() < respond_to_all_probability
            
        return False
        
    def _parse_bot_qq_numbers(self, qq_numbers_str: str) -> List[str]:
        """解析机器人QQ号字符串"""
        if not qq_numbers_str:
            return []
            
        numbers = re.split(r'[,，\s]+', qq_numbers_str)
        return [num.strip() for num in numbers if num.strip()]
        
    async def _handle_notice_event(self, event_data: Dict[str, Any]):
        """处理通知事件"""
        notice_type = event_data.get("notice_type")
        self.logger.debug(f"收到通知事件: {notice_type}")
        
    async def _handle_request_event(self, event_data: Dict[str, Any]):
        """处理请求事件"""
        request_type = event_data.get("request_type")
        self.logger.debug(f"收到请求事件: {request_type}")
        
    async def _handle_meta_event(self, event_data: Dict[str, Any]):
        """处理元事件"""
        meta_event_type = event_data.get("meta_event_type")
        if meta_event_type == "heartbeat":
            pass
            
    async def send_message_async(self, response_data: Dict[str, Any]):
        """
        异步发送消息（主接口）
        
        Args:
            response_data: 响应数据
        """
        try:
            chat_id = response_data.get("chat_id")
            content = response_data.get("content", "")
            
            if not chat_id or not content:
                self.logger.warning("发送消息失败: 缺少chat_id或content")
                return
                
            # 启动或获取该chat_id的发送队列
            if chat_id not in self.send_queues:
                self.send_queues[chat_id] = asyncio.Queue(maxsize=100)
                # 启动单消费者
                self.send_consumers[chat_id] = asyncio.create_task(
                    self._send_consumer_loop(chat_id)
                )
            
            # 将消息放入队列
            await self.send_queues[chat_id].put(response_data)
            self.logger.debug(f"消息已加入发送队列: {chat_id}")
            
        except Exception as e:
            self.logger.error(f"发送消息失败: {e}")
            
    async def _send_consumer_loop(self, chat_id: str):
        """发送消费者循环（单消费者模式）"""
        queue = self.send_queues.get(chat_id)
        if not queue:
            return
            
        self.logger.debug(f"启动发送消费者: {chat_id}")
        
        while self.is_running:
            try:
                # 从队列获取消息
                response_data = await queue.get()
                
                # 发送消息
                await self._send_message_direct(response_data)
                
                # 标记任务完成
                queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"发送消费者异常 {chat_id}: {e}")
                await asyncio.sleep(1)
                
        self.logger.debug(f"发送消费者停止: {chat_id}")
        
    async def _send_message_direct(self, response_data: Dict[str, Any]):
        """直接发送消息"""
        try:
            chat_id = response_data.get("chat_id")
            
            # 解析chat_id获取目标类型和ID
            target_type, target_id = self._parse_chat_id(chat_id)
            
            if not target_type or not target_id:
                self.logger.warning(f"解析chat_id失败: {chat_id}")
                return
                
            # 转换消息格式为OneBot消息段
            message_segments = self._convert_to_onebot_format(response_data.get("content", ""))
            
            if target_type == "private":
                # 发送私聊消息
                await self._send_private_message_async(target_id, message_segments)
                
            elif target_type == "group":
                # 发送群聊消息
                await self._send_group_message_async(target_id, message_segments)
                
        except Exception as e:
            self.logger.error(f"发送消息失败: {e}")
            
    def _parse_chat_id(self, chat_id: str) -> tuple:
        """解析chat_id"""
        parts = chat_id.split('_')
        
        if len(parts) >= 3:
            platform = parts[0]
            target_type = parts[1]
            target_id = parts[2]
            
            return target_type, target_id
            
        return None, None
        
    def _convert_to_onebot_format(self, content: Any) -> List[Dict[str, Any]]:
        """从OpenAI格式转换回OneBot消息段"""
        message_segments = []
        
        if isinstance(content, str):
            # 纯文本
            if content:
                message_segments.append({
                    "type": "text",
                    "data": {"text": content}
                })
                
        elif isinstance(content, list):
            # 多模态消息
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
        
    async def _send_private_message_async(self, user_id: str, message: List[Dict[str, Any]]):
        """异步发送私聊消息"""
        try:
            if not self.ws_connection or not self.is_connected:
                self.logger.error(f"WebSocket连接未建立，无法发送私聊消息到: {user_id}")
                return
                
            # 构建OneBot API请求
            api_request = {
                "action": "send_private_msg",
                "params": {
                    "user_id": int(user_id),
                    "message": message,
                    "auto_escape": False
                },
                "echo": f"private_{user_id}_{int(time.time())}"
            }
            
            # 通过WebSocket发送API请求
            await self.ws_connection.send(json.dumps(api_request))
            self.logger.info(f"私聊消息已通过WebSocket发送: {user_id}")
            
        except Exception as e:
            self.logger.error(f"通过WebSocket发送私聊消息异常: {e}")
            
            # 尝试备用HTTP方法
            try:
                api_url = self.config.get("connection", {}).get("api_url", "http://127.0.0.1:8080")
                endpoint = f"{api_url}/send_private_msg"
                
                payload = {
                    "user_id": int(user_id),
                    "message": message,
                    "auto_escape": False
                }
                
                # 发送HTTP请求
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
            
    async def _send_group_message_async(self, group_id: str, message: List[Dict[str, Any]]):
        """异步发送群聊消息"""
        try:
            if not self.ws_connection or not self.is_connected:
                self.logger.error(f"WebSocket连接未建立，无法发送群聊消息到: {group_id}")
                return
                
            # 构建OneBot API请求
            api_request = {
                "action": "send_group_msg",
                "params": {
                    "group_id": int(group_id),
                    "message": message,
                    "auto_escape": False
                },
                "echo": f"group_{group_id}_{int(time.time())}"
            }
            
            # 通过WebSocket发送API请求
            await self.ws_connection.send(json.dumps(api_request))
            self.logger.info(f"群聊消息已通过WebSocket发送: {group_id}")
            
        except Exception as e:
            self.logger.error(f"通过WebSocket发送群聊消息异常: {e}")
            
            # 备用HTTP方法
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
            
    async def is_connected_async(self) -> bool:
        """异步检查连接状态"""
        return self.is_connected and self.is_running
        
    async def stop(self):
        """异步停止客户端"""
        self.is_running = False
        
        # 取消接收任务
        if self.receive_task:
            self.receive_task.cancel()
            try:
                await self.receive_task
            except asyncio.CancelledError:
                pass
                
        # 取消所有发送消费者
        for chat_id, task in self.send_consumers.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
                
        # 关闭WebSocket连接
        if self.ws_connection:
            await self.ws_connection.close()
            
        # 关闭HTTP会话
        if self.http_session:
            await self.http_session.close()
            
        self.is_connected = False
        
        self.logger.info("异步NapCat客户端已停止")