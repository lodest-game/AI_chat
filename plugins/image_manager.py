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
from concurrent.futures import ThreadPoolExecutor


@dataclass
class ImageCacheItem:
    image_id: str
    chat_id: str
    url: str
    base64_data: str
    mime_type: str
    created_at: float
    last_accessed: float
    is_processing: bool
    size: int


class ImageManager:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.base_dir = Path(__file__).parent
        self.config_file = self.base_dir / "image_config.json"
        self.config = {}
        
        self.default_config = {
            "cache": {
                "default_ttl_seconds": 60,
                "privilege_ttl_seconds": 1800,
                "default_max_per_chat": 10,
                "privilege_max_per_chat": 20,
            },
            "concurrency": {
                "max_concurrent_downloads": 8,
                "max_encoding_threads": 4,
                "download_timeout": 30
            },
            "privilege": []
        }
        
        self.image_cache = {}
        self.chat_cache = {}
        self.processing_tasks = {}
        self.download_semaphore = None
        self.thread_pool = None
        self.is_running = False
        self.cleanup_task = None
        self.lock = asyncio.Lock()
        
        self.stats = {
            "total_processed": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "downloads": 0,
            "encodings": 0,
            "errors": 0,
            "encoding_thread_usage": 0
        }
        
    async def initialize(self):
        await self._load_config()
        
        max_concurrent_downloads = self.config.get("concurrency", {}).get("max_concurrent_downloads", 8)
        self.download_semaphore = asyncio.Semaphore(max_concurrent_downloads)
        
        max_encoding_threads = self.config.get("concurrency", {}).get("max_encoding_threads", 4)
        self.thread_pool = ThreadPoolExecutor(max_workers=max_encoding_threads, thread_name_prefix="img_encoder")
        
        self.is_running = True
        self.cleanup_task = asyncio.create_task(self._cleanup_daemon())
        
    async def _load_config(self):
        if self.config_file.exists():
            try:
                loop = asyncio.get_event_loop()
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config = await loop.run_in_executor(None, json.load, f)
            except Exception as e:
                self.logger.error(f"加载图片配置文件失败: {e}")
                self.config = self.default_config
                await self._save_config()
        else:
            self.config = self.default_config
            await self._save_config()
            
    async def _save_config(self):
        try:
            loop = asyncio.get_event_loop()
            with open(self.config_file, 'w', encoding='utf-8') as f:
                await loop.run_in_executor(
                    None, 
                    lambda: json.dump(self.config, f, ensure_ascii=False, indent=2)
                )
        except Exception as e:
            self.logger.error(f"保存图片配置文件失败: {e}")
            
    def _generate_image_id(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()
        
    def _is_privilege_chat(self, chat_id: str) -> bool:
        privilege_list = self.config.get("privilege", [])
        return chat_id in privilege_list
        
    def _get_chat_cache_config(self, chat_id: str) -> Tuple[int, int]:
        is_privilege = self._is_privilege_chat(chat_id)
        
        if is_privilege:
            ttl = self.config.get("cache", {}).get("privilege_ttl_seconds", 1800)
            max_items = self.config.get("cache", {}).get("privilege_max_per_chat", 20)
        else:
            ttl = self.config.get("cache", {}).get("default_ttl_seconds", 60)
            max_items = self.config.get("cache", {}).get("default_max_per_chat", 10)
            
        return ttl, max_items
        
    async def analyze_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            chat_id = message_data.get("chat_id")
            content = message_data.get("content", "")
            
            if not chat_id or not content:
                return {"success": False, "error": "无效的消息数据"}
                
            image_urls = self._extract_image_urls(content)
            
            if not image_urls:
                return {"success": True, "has_images": False}
                
            process_tasks = []
            for url in image_urls:
                task = asyncio.create_task(self._process_image_url(chat_id, url))
                process_tasks.append(task)
                
            results = await asyncio.gather(*process_tasks, return_exceptions=True)
            successful_images = []
            
            for result in results:
                if isinstance(result, dict) and result.get("success"):
                    successful_images.append({
                        "image_id": result.get("image_id"),
                        "url": result.get("url")
                    })
                    
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
        image_urls = []
        
        if not isinstance(content, list):
            return image_urls
        
        for item in content:
            if not (isinstance(item, dict) and item.get("type") == "image_url"):
                continue
                
            image_url = item.get("image_url", {})
            url = ""
            if isinstance(image_url, dict):
                url = image_url.get("url", "")
            elif isinstance(image_url, str):
                url = image_url
                
            if url and url.startswith(("http://", "https://")):
                image_urls.append(url)
                
        return image_urls
        
    async def _process_image_url(self, chat_id: str, url: str) -> Dict[str, Any]:
        try:
            image_id = self._generate_image_id(url)
            
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
            
            async with self.lock:
                if url in self.processing_tasks:
                    task = self.processing_tasks[url]
                    try:
                        return await task
                    except Exception as e:
                        self.logger.error(f"等待现有任务失败: {e}")
                        
            task = asyncio.create_task(self._download_and_encode_image(chat_id, url, image_id))
            
            async with self.lock:
                self.processing_tasks[url] = task
                
            try:
                result = await task
                return result
            finally:
                async with self.lock:
                    if url in self.processing_tasks:
                        del self.processing_tasks[url]
                        
        except Exception as e:
            self.logger.error(f"处理图片URL失败: {e}")
            self.stats["errors"] += 1
            return {"success": False, "error": str(e), "url": url}
            
    async def _download_and_encode_image(self, chat_id: str, url: str, image_id: str) -> Dict[str, Any]:
        try:
            async with self.download_semaphore:
                image_data, mime_type = await self._download_image(url)
                
            self.stats["downloads"] += 1
            
            if not image_data:
                return {"success": False, "error": "下载失败", "url": url}
                
            loop = asyncio.get_event_loop()
            base64_data = await loop.run_in_executor(
                self.thread_pool,
                self._encode_to_base64_sync,
                image_data,
                mime_type
            )
            
            self.stats["encodings"] += 1
            
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
            
            await self._save_to_cache(cache_item)
            self.stats["total_processed"] += 1
            
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
        try:
            base64_encoded = base64.b64encode(image_data).decode('utf-8')
            
            if mime_type.startswith('image/'):
                image_type = mime_type[6:]
            else:
                image_type = 'jpeg'
                
            return f"data:image/{image_type};base64,{base64_encoded}"
            
        except Exception as e:
            self.logger.error(f"base64编码失败: {e}")
            raise
            
    async def _download_image(self, url: str) -> Tuple[bytes, str]:
        try:
            timeout = aiohttp.ClientTimeout(
                total=self.config.get("concurrency", {}).get("download_timeout", 30)
            )
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        image_data = await response.read()
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
        async with self.lock:
            if image_id in self.image_cache:
                cache_item = self.image_cache[image_id]
                
                if cache_item.chat_id == chat_id:
                    cache_item.last_accessed = time.time()
                    
                    if chat_id in self.chat_cache and image_id in self.chat_cache[chat_id]:
                        self.chat_cache[chat_id].move_to_end(image_id)
                        
                    return cache_item
                    
        return None
        
    async def get_image_base64(self, chat_id: str, url: str) -> Optional[str]:
        try:
            image_id = self._generate_image_id(url)
            cache_item = await self._get_from_cache(chat_id, image_id)
            if cache_item:
                return cache_item.base64_data
            return None
            
        except Exception as e:
            self.logger.error(f"获取图片base64失败: {e}")
            return None
            
    async def _save_to_cache(self, cache_item: ImageCacheItem):
        async with self.lock:
            self.image_cache[cache_item.image_id] = cache_item
            
            chat_id = cache_item.chat_id
            if chat_id not in self.chat_cache:
                self.chat_cache[chat_id] = OrderedDict()
                
            self.chat_cache[chat_id][cache_item.image_id] = cache_item
            
            ttl, max_items = self._get_chat_cache_config(chat_id)
            
            if len(self.chat_cache[chat_id]) > max_items:
                while len(self.chat_cache[chat_id]) > max_items:
                    oldest_image_id, _ = self.chat_cache[chat_id].popitem(last=False)
                    if oldest_image_id in self.image_cache:
                        del self.image_cache[oldest_image_id]
        
    async def _cleanup_daemon(self):
        while self.is_running:
            try:
                await asyncio.sleep(30)
                current_time = time.time()
                items_to_remove = []
                
                async with self.lock:
                    for image_id, cache_item in list(self.image_cache.items()):
                        chat_id = cache_item.chat_id
                        ttl, _ = self._get_chat_cache_config(chat_id)
                        inactive_time = current_time - cache_item.last_accessed
                        if inactive_time >= ttl:
                            items_to_remove.append((image_id, chat_id))
                            
                    for image_id, chat_id in items_to_remove:
                        if image_id in self.image_cache:
                            del self.image_cache[image_id]
                        if chat_id in self.chat_cache and image_id in self.chat_cache[chat_id]:
                            del self.chat_cache[chat_id][image_id]
                            
                    for chat_id in list(self.chat_cache.keys()):
                        if not self.chat_cache[chat_id]:
                            del self.chat_cache[chat_id]
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"清理守护任务异常: {e}")
                
    async def get_cache_status(self) -> Dict[str, Any]:
        async with self.lock:
            thread_pool_status = {}
            if self.thread_pool:
                thread_pool_status = {
                    "max_workers": self.thread_pool._max_workers,
                    "active_threads": self.thread_pool._threads - len(self.thread_pool._idle_semaphore._value),
                }
            
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
        try:
            async with self.lock:
                if chat_id in self.chat_cache:
                    for image_id in list(self.chat_cache[chat_id].keys()):
                        if image_id in self.image_cache:
                            del self.image_cache[image_id]
                    del self.chat_cache[chat_id]
                return True
                
        except Exception as e:
            self.logger.error(f"清理对话缓存失败: {e}")
            return False
            
    async def clear_all_cache(self) -> bool:
        try:
            async with self.lock:
                self.image_cache.clear()
                self.chat_cache.clear()
                return True
                
        except Exception as e:
            self.logger.error(f"清理所有缓存失败: {e}")
            return False
            
    async def shutdown(self):
        self.is_running = False
        
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
                
        async with self.lock:
            for url, task in self.processing_tasks.items():
                task.cancel()
                
        await self.clear_all_cache()
        
        if self.thread_pool:
            self.thread_pool.shutdown(wait=True)