#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_manager.py - 混合架构图片管理器
异步并发处理IO，多线程并行处理CPU密集型转码
"""

import asyncio
import aiohttp
import base64
import logging
import time
import hashlib
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from collections import OrderedDict
import re
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor


@dataclass
class ImageCacheItem:
    """图片缓存项"""
    image_id: str  # URL的MD5哈希
    chat_id: str   # 所属对话ID
    url: str       # 原始URL
    base64_data: str  # base64编码的图片数据
    mime_type: str    # 图片MIME类型
    created_at: float  # 创建时间戳
    last_accessed: float  # 最后访问时间
    is_processing: bool  # 是否正在处理中
    size: int  # 数据大小（字节）


class ImageManager:
    """混合架构图片管理器 - 异步IO + 多线程转码"""
    
    def __init__(self):
        """初始化图片管理器"""
        self.logger = logging.getLogger(__name__)
        
        # 目录配置
        self.base_dir = Path(__file__).parent
        self.config_file = self.base_dir / "image_config.json"
        
        # 配置数据
        self.config = {}
        
        # 默认配置
        self.default_config = {
            "cache": {
                "default_ttl_seconds": 60,  # 默认缓存时间
                "privilege_ttl_seconds": 1800,  # 特权缓存时间
                "default_max_per_chat": 10,  # 默认每对话最大缓存数
                "privilege_max_per_chat": 20,  # 特权每对话最大缓存数
            },
            "concurrency": {
                "max_concurrent_downloads": 8,   # 最大并发下载数
                "max_encoding_threads": 4,       # 最大编码线程数
                "download_timeout": 30           # 下载超时时间
            },
            "privilege": []  # 特权对话ID列表
        }
        
        # 缓存存储
        self.image_cache = {}  # image_id -> ImageCacheItem
        self.chat_cache = {}   # chat_id -> OrderedDict[image_id -> ImageCacheItem]
        
        # 处理任务管理
        self.processing_tasks = {}  # url -> asyncio.Task
        
        # 并发控制
        self.download_semaphore = None  # 异步下载信号量
        self.thread_pool = None         # 线程池用于转码
        
        # 运行标志
        self.is_running = False
        
        # 清理守护任务
        self.cleanup_task = None
        
        # 锁
        self.lock = asyncio.Lock()
        
        # 统计数据
        self.stats = {
            "total_processed": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "downloads": 0,
            "encodings": 0,
            "errors": 0,
            "encoding_thread_usage": 0  # 线程池使用率统计
        }
        
    async def initialize(self):
        """异步初始化图片管理器"""
        self.logger.info("初始化混合架构图片管理器...")
        
        # 加载配置
        await self._load_config()
        
        # 初始化异步并发控制信号量
        max_concurrent_downloads = self.config.get("concurrency", {}).get("max_concurrent_downloads", 8)
        self.download_semaphore = asyncio.Semaphore(max_concurrent_downloads)
        
        # 初始化线程池用于转码
        max_encoding_threads = self.config.get("concurrency", {}).get("max_encoding_threads", 4)
        self.thread_pool = ThreadPoolExecutor(max_workers=max_encoding_threads, thread_name_prefix="img_encoder")
        
        # 启动清理守护任务
        self.is_running = True
        self.cleanup_task = asyncio.create_task(self._cleanup_daemon())
        
        self.logger.info(f"混合架构图片管理器初始化完成，下载并发数: {max_concurrent_downloads}, 编码线程数: {max_encoding_threads}")
        
    async def _load_config(self):
        """异步加载配置文件"""
        if self.config_file.exists():
            try:
                loop = asyncio.get_event_loop()
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config = await loop.run_in_executor(None, json.load, f)
                self.logger.info(f"图片配置文件已加载: {self.config_file}")
            except Exception as e:
                self.logger.error(f"加载图片配置文件失败: {e}")
                self.config = self.default_config
                await self._save_config()
        else:
            self.config = self.default_config
            await self._save_config()
            
    async def _save_config(self):
        """保存配置文件"""
        try:
            loop = asyncio.get_event_loop()
            with open(self.config_file, 'w', encoding='utf-8') as f:
                await loop.run_in_executor(
                    None, 
                    lambda: json.dump(self.config, f, ensure_ascii=False, indent=2)
                )
            self.logger.debug(f"图片配置文件已保存: {self.config_file}")
        except Exception as e:
            self.logger.error(f"保存图片配置文件失败: {e}")
            
    def _generate_image_id(self, url: str) -> str:
        """生成图片ID（URL的MD5哈希）"""
        return hashlib.md5(url.encode()).hexdigest()
        
    def _is_privilege_chat(self, chat_id: str) -> bool:
        """检查是否为特权对话"""
        privilege_list = self.config.get("privilege", [])
        return chat_id in privilege_list
        
    def _get_chat_cache_config(self, chat_id: str) -> Tuple[int, int]:
        """获取对话缓存配置"""
        is_privilege = self._is_privilege_chat(chat_id)
        
        if is_privilege:
            ttl = self.config.get("cache", {}).get("privilege_ttl_seconds", 1800)
            max_items = self.config.get("cache", {}).get("privilege_max_per_chat", 20)
        else:
            ttl = self.config.get("cache", {}).get("default_ttl_seconds", 60)
            max_items = self.config.get("cache", {}).get("default_max_per_chat", 10)
            
        return ttl, max_items
        
    async def analyze_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        分析消息是否包含图片URL
        
        Args:
            message_data: NapCat客户端发送的消息数据
            
        Returns:
            分析结果
        """
        try:
            chat_id = message_data.get("chat_id")
            content = message_data.get("content", "")
            
            if not chat_id or not content:
                return {"success": False, "error": "无效的消息数据"}
                
            # 提取所有图片URL
            image_urls = self._extract_image_urls(content)
            
            if not image_urls:
                return {"success": True, "has_images": False}
                
            # 异步处理所有图片
            process_tasks = []
            for url in image_urls:
                task = asyncio.create_task(
                    self._process_image_url(chat_id, url)
                )
                process_tasks.append(task)
                
            # 等待所有处理任务完成
            results = await asyncio.gather(*process_tasks, return_exceptions=True)
            
            # 收集成功的图片ID
            successful_images = []
            for result in results:
                if isinstance(result, dict) and result.get("success"):
                    successful_images.append({
                        "image_id": result.get("image_id"),
                        "url": result.get("url")
                    })
                    
            self.logger.info(f"消息分析完成: chat_id={chat_id}, 图片数={len(successful_images)}")
            
            return {
                "success": True,
                "has_images": True,
                "image_count": len(successful_images),
                "images": successful_images
            }
            
        except Exception as e:
            self.logger.error(f"分析消息失败: {e}")
            return {"success": False, "error": str(e)}
            
    def _extract_image_urls(self, content: Any) -> List[str]:
        """从消息内容中提取图片URL"""
        image_urls = []
        
        # 只处理数组类型的内容
        if not isinstance(content, list):
            return image_urls
        
        for item in content:
            # 检查是否为图片类型
            if not (isinstance(item, dict) and item.get("type") == "image_url"):
                continue
                
            # 获取图片URL
            image_url = item.get("image_url", {})
            url = ""
            if isinstance(image_url, dict):
                url = image_url.get("url", "")
            elif isinstance(image_url, str):
                url = image_url
                
            # 验证URL格式
            if url and url.startswith(("http://", "https://")):
                image_urls.append(url)
                
        return image_urls
        
    async def _process_image_url(self, chat_id: str, url: str) -> Dict[str, Any]:
        """
        处理单个图片URL
        
        Args:
            chat_id: 对话ID
            url: 图片URL
            
        Returns:
            处理结果
        """
        try:
            image_id = self._generate_image_id(url)
            
            # 检查缓存中是否已有
            cache_item = await self._get_from_cache(chat_id, image_id)
            if cache_item:
                self.stats["cache_hits"] += 1
                return {
                    "success": True,
                    "image_id": image_id,
                    "url": url,
                    "from_cache": True
                }
                
            self.stats["cache_misses"] += 1
            
            # 检查是否正在处理
            async with self.lock:
                if url in self.processing_tasks:
                    task = self.processing_tasks[url]
                    # 等待现有任务完成
                    try:
                        result = await task
                        return result
                    except Exception as e:
                        self.logger.error(f"等待现有任务失败: {e}")
                        
            # 创建处理任务
            task = asyncio.create_task(
                self._download_and_encode_image(chat_id, url, image_id)
            )
            
            # 注册处理任务
            async with self.lock:
                self.processing_tasks[url] = task
                
            # 等待任务完成
            try:
                result = await task
                return result
            finally:
                # 清理任务记录
                async with self.lock:
                    if url in self.processing_tasks:
                        del self.processing_tasks[url]
                        
        except Exception as e:
            self.logger.error(f"处理图片URL失败: {e}")
            self.stats["errors"] += 1
            return {"success": False, "error": str(e), "url": url}
            
    async def _download_and_encode_image(self, chat_id: str, url: str, image_id: str) -> Dict[str, Any]:
        """
        下载并编码图片
        
        Args:
            chat_id: 对话ID
            url: 图片URL
            image_id: 图片ID
            
        Returns:
            处理结果
        """
        try:
            self.logger.debug(f"开始下载图片: {url}")
            
            # 异步下载图片（使用信号量控制并发）
            async with self.download_semaphore:
                image_data, mime_type = await self._download_image(url)
                
            self.stats["downloads"] += 1
            
            if not image_data:
                return {"success": False, "error": "下载失败", "url": url}
                
            # 使用线程池并行执行base64编码（CPU密集型操作）
            loop = asyncio.get_event_loop()
            base64_data = await loop.run_in_executor(
                self.thread_pool,
                self._encode_to_base64_sync,
                image_data,
                mime_type
            )
            
            self.stats["encodings"] += 1
            
            # 创建缓存项
            cache_item = ImageCacheItem(
                image_id=image_id,
                chat_id=chat_id,
                url=url,
                base64_data=base64_data,
                mime_type=mime_type,
                created_at=time.time(),
                last_accessed=time.time(),
                is_processing=False,
                size=len(base64_data)
            )
            
            # 保存到缓存
            await self._save_to_cache(cache_item)
            
            self.stats["total_processed"] += 1
            
            self.logger.debug(f"图片处理完成: {url}, size={len(image_data)} bytes")
            
            return {
                "success": True,
                "image_id": image_id,
                "url": url,
                "mime_type": mime_type,
                "from_cache": False
            }
            
        except Exception as e:
            self.logger.error(f"下载编码图片失败: {e}")
            self.stats["errors"] += 1
            return {"success": False, "error": str(e), "url": url}
            
    def _encode_to_base64_sync(self, image_data: bytes, mime_type: str) -> str:
        """同步版本的base64编码（在线程池中运行）"""
        try:
            # 编码为base64
            base64_encoded = base64.b64encode(image_data).decode('utf-8')
            
            # 格式化为OpenAI API格式: data:image/{type};base64,{data}
            # 提取主要MIME类型
            if mime_type.startswith('image/'):
                image_type = mime_type[6:]  # 移除'image/'前缀
            else:
                image_type = 'jpeg'  # 默认jpeg
                
            # 构建完整的base64数据URI
            base64_data = f"data:image/{image_type};base64,{base64_encoded}"
            
            return base64_data
            
        except Exception as e:
            self.logger.error(f"base64编码失败: {e}")
            raise
            
    async def _download_image(self, url: str) -> Tuple[bytes, str]:
        """异步下载图片"""
        try:
            timeout = aiohttp.ClientTimeout(
                total=self.config.get("concurrency", {}).get("download_timeout", 30)
            )
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        # 获取图片数据
                        image_data = await response.read()
                        
                        # 获取MIME类型
                        content_type = response.headers.get('Content-Type', 'image/jpeg')
                        mime_type = content_type.split(';')[0].strip()
                        
                        return image_data, mime_type
                    else:
                        self.logger.error(f"下载图片失败: HTTP {response.status}, URL: {url}")
                        return None, None
                        
        except asyncio.TimeoutError:
            self.logger.error(f"下载图片超时: {url}")
            return None, None
        except Exception as e:
            self.logger.error(f"下载图片异常: {e}, URL: {url}")
            return None, None
            
    async def _get_from_cache(self, chat_id: str, image_id: str) -> Optional[ImageCacheItem]:
        """从缓存获取图片数据"""
        async with self.lock:
            if image_id in self.image_cache:
                cache_item = self.image_cache[image_id]
                
                # 检查是否属于当前对话
                if cache_item.chat_id == chat_id:
                    # 更新访问时间
                    cache_item.last_accessed = time.time()
                    
                    # 更新chat_cache中的LRU顺序
                    if chat_id in self.chat_cache and image_id in self.chat_cache[chat_id]:
                        self.chat_cache[chat_id].move_to_end(image_id)
                        
                    return cache_item
                    
        return None
        
    async def get_image_base64(self, chat_id: str, url: str) -> Optional[str]:
        """
        获取图片的base64数据
        
        Args:
            chat_id: 对话ID
            url: 图片URL
            
        Returns:
            base64数据，如果失败则返回None
        """
        try:
            image_id = self._generate_image_id(url)
            
            # 从缓存获取
            cache_item = await self._get_from_cache(chat_id, image_id)
            if cache_item:
                return cache_item.base64_data
                
            # 缓存未命中，返回None
            return None
            
        except Exception as e:
            self.logger.error(f"获取图片base64失败: {e}")
            return None
            
    async def _save_to_cache(self, cache_item: ImageCacheItem):
        """保存到缓存"""
        async with self.lock:
            # 保存到全局缓存
            self.image_cache[cache_item.image_id] = cache_item
            
            # 保存到对话缓存
            chat_id = cache_item.chat_id
            if chat_id not in self.chat_cache:
                self.chat_cache[chat_id] = OrderedDict()
                
            self.chat_cache[chat_id][cache_item.image_id] = cache_item
            
            # 获取对话缓存配置
            ttl, max_items = self._get_chat_cache_config(chat_id)
            
            # 检查是否超过最大数量
            if len(self.chat_cache[chat_id]) > max_items:
                # 移除最久未访问的项
                while len(self.chat_cache[chat_id]) > max_items:
                    oldest_image_id, _ = self.chat_cache[chat_id].popitem(last=False)
                    if oldest_image_id in self.image_cache:
                        del self.image_cache[oldest_image_id]
                        
            self.logger.debug(f"图片已缓存: {cache_item.image_id}, chat_id={chat_id}")
            
    async def replace_urls_with_base64(self, chat_id: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        将消息中的URL替换为base64数据
        
        Args:
            chat_id: 对话ID
            messages: 消息列表
            
        Returns:
            处理后的消息列表
        """
        try:
            processed_messages = []
            
            for message in messages:
                if not isinstance(message, dict):
                    processed_messages.append(message)
                    continue
                    
                role = message.get("role")
                content = message.get("content", "")
                
                # 只处理用户消息
                if role != "user":
                    processed_messages.append(message)
                    continue
                    
                # 处理字符串内容 - 简化逻辑，不再处理CQ码
                if isinstance(content, str):
                    # ⚡ 简化：如果内容是字符串且包含图片URL，现在应该已经由客户端处理为结构化格式
                    # 我们直接保留字符串内容，不尝试提取图片
                    processed_messages.append(message)
                    continue
                        
                # 处理数组内容（OpenAI API格式）
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
                                    # 网络URL，需要替换为base64
                                    base64_data = await self.get_image_base64(chat_id, url)
                                    if base64_data:
                                        new_content.append({
                                            "type": "image_url",
                                            "image_url": {"url": base64_data}
                                        })
                                    else:
                                        # ⚡ 修复：移除无法转换的URL
                                        self.logger.warning(f"图片URL无法转换为base64，已移除: {url[:50]}...")
                                elif url and url.startswith("data:image/"):
                                    # 已经是base64格式，直接使用
                                    new_content.append(item)
                                else:
                                    # 无效的URL，跳过
                                    self.logger.warning(f"无效的图片URL格式，已移除: {url}")
                                    continue
                            else:
                                new_content.append(item)
                        else:
                            new_content.append(item)
                            
                    # 如果处理后new_content为空，保留原始消息
                    if new_content:
                        processed_message = message.copy()
                        processed_message["content"] = new_content
                        processed_messages.append(processed_message)
                    else:
                        processed_messages.append(message)
                else:
                    processed_messages.append(message)
                    
            return processed_messages
            
        except Exception as e:
            self.logger.error(f"替换URL为base64失败: {e}")
            return messages
        
    async def _cleanup_daemon(self):
        """异步清理守护任务"""
        while self.is_running:
            try:
                await asyncio.sleep(30)  # 每30秒清理一次
                
                current_time = time.time()
                items_to_remove = []
                
                async with self.lock:
                    # 检查所有缓存项
                    for image_id, cache_item in list(self.image_cache.items()):
                        chat_id = cache_item.chat_id
                        ttl, _ = self._get_chat_cache_config(chat_id)
                        
                        # 检查是否过期
                        inactive_time = current_time - cache_item.last_accessed
                        if inactive_time >= ttl:
                            items_to_remove.append((image_id, chat_id))
                            
                    # 清理过期项
                    for image_id, chat_id in items_to_remove:
                        if image_id in self.image_cache:
                            del self.image_cache[image_id]
                            
                        if chat_id in self.chat_cache and image_id in self.chat_cache[chat_id]:
                            del self.chat_cache[chat_id][image_id]
                            
                    # 清理空对话缓存
                    for chat_id in list(self.chat_cache.keys()):
                        if not self.chat_cache[chat_id]:
                            del self.chat_cache[chat_id]
                            
                if items_to_remove:
                    self.logger.debug(f"清理了 {len(items_to_remove)} 个过期缓存项")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"清理守护任务异常: {e}")
                
    async def get_cache_status(self) -> Dict[str, Any]:
        """获取缓存状态"""
        async with self.lock:
            # 统计线程池使用情况
            thread_pool_status = {
                "max_workers": self.thread_pool._max_workers if self.thread_pool else 0,
                "active_threads": self.thread_pool._threads - len(self.thread_pool._idle_semaphore._value) if self.thread_pool else 0,
            } if self.thread_pool else {}
            
            status = {
                "total_cached": len(self.image_cache),
                "total_chats": len(self.chat_cache),
                "stats": self.stats.copy(),
                "cache_by_chat": {},
                "concurrency_limits": {
                    "max_concurrent_downloads": self.download_semaphore._value if self.download_semaphore else 0,
                    "thread_pool": thread_pool_status
                }
            }
            
            for chat_id, cache_dict in self.chat_cache.items():
                status["cache_by_chat"][chat_id] = {
                    "count": len(cache_dict),
                    "is_privilege": self._is_privilege_chat(chat_id)
                }
                
            return status
            
    async def clear_chat_cache(self, chat_id: str) -> bool:
        """清理指定对话的缓存"""
        try:
            async with self.lock:
                if chat_id in self.chat_cache:
                    # 从全局缓存中移除
                    for image_id in list(self.chat_cache[chat_id].keys()):
                        if image_id in self.image_cache:
                            del self.image_cache[image_id]
                            
                    # 清理对话缓存
                    del self.chat_cache[chat_id]
                    
                self.logger.info(f"已清理对话缓存: {chat_id}")
                return True
                
        except Exception as e:
            self.logger.error(f"清理对话缓存失败: {e}")
            return False
            
    async def clear_all_cache(self) -> bool:
        """清理所有缓存"""
        try:
            async with self.lock:
                self.image_cache.clear()
                self.chat_cache.clear()
                
                self.logger.info("已清理所有图片缓存")
                return True
                
        except Exception as e:
            self.logger.error(f"清理所有缓存失败: {e}")
            return False
            
    async def shutdown(self):
        """关闭图片管理器"""
        self.logger.info("正在关闭混合架构图片管理器...")
        
        self.is_running = False
        
        # 等待清理任务结束
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
                
        # 取消所有处理任务
        async with self.lock:
            for url, task in self.processing_tasks.items():
                task.cancel()
                
        # 清理缓存
        await self.clear_all_cache()
        
        # 关闭线程池
        if self.thread_pool:
            self.thread_pool.shutdown(wait=True)
            self.logger.info("编码线程池已关闭")
        
        self.logger.info("混合架构图片管理器已关闭")