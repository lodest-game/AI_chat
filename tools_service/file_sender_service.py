#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
file_upload_service.py - 独立文件上传工具
直接通过 WebSocket 连接 NapCat，上传缓存文件并清理本地缓存
"""

import json
import asyncio
import time
from pathlib import Path
import re
import websockets  # 需要安装：pip install websockets

# ==================== 工具定义 ====================
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "upload_cached_file",
            "description": "将之前缓存的文件上传到当前对话中，上传后自动删除本地缓存",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "对话ID，例如 'qq_group_123456' 或 'qq_private_789'"
                    },
                    "filename": {
                        "type": "string",
                        "description": "要上传的文件名（必须与缓存时使用的文件名一致）"
                    }
                },
                "required": ["chat_id", "filename"]
            }
        }
    }
]

# ==================== 工具配置 ====================
TOOL_CONFIGS = {
    "upload_cached_file": {
        "timeout": 60.0,      # 上传可能较慢，设长一些
        "max_retries": 2,
        "enabled": True
    }
}

# ==================== 辅助函数 ====================

def _parse_chat_id(chat_id: str):
    """
    解析 chat_id，返回 (target_type, target_id)
    支持格式: qq_group_123456 -> ('group', '123456')
               qq_private_789  -> ('private', '789')
    """
    parts = chat_id.split('_')
    if len(parts) >= 3 and parts[0] == 'qq':
        platform = parts[0]          # 平台，目前只支持 qq
        target_type = parts[1]        # group 或 private
        target_id = parts[2]          # 纯数字 ID
        return target_type, target_id
    return None, None

def _get_napcat_ws_url():
    """
    读取 NapCat 客户端配置文件，获取 WebSocket 地址
    默认地址: ws://127.0.0.1:8080
    """
    config_path = Path(__file__).parent / "NapCat_client.json"
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return config.get("connection", {}).get("ws_url", "ws://127.0.0.1:8080")
        except Exception:
            pass
    return "ws://127.0.0.1:8080"

def _delete_file_sync(file_path: Path):
    """同步删除文件（在 executor 中执行）"""
    file_path.unlink()

# ==================== 工具处理函数 ====================

async def upload_cached_file(chat_id: str, filename: str) -> str:
    """
    上传缓存文件到对应聊天，并清理本地缓存
    """
    try:
        # 1. 解析目标类型和 ID
        target_type, target_id = _parse_chat_id(chat_id)
        if not target_type or not target_id:
            return f"【上传失败】无法从 chat_id 解析目标信息: {chat_id}"

        # 2. 安全处理文件名
        safe_filename = Path(filename).name
        if safe_filename != filename:
            return f"【上传失败】文件名不能包含路径分隔符: {filename}"

        # 3. 构建本地文件路径
        base_dir = Path(__file__).parent / "cached_files" / chat_id
        file_path = base_dir / safe_filename

        if not file_path.exists():
            return f"【上传失败】本地缓存文件不存在: {safe_filename}（对话：{chat_id}）"

        # 4. 获取 NapCat WebSocket 地址
        ws_url = _get_napcat_ws_url()

        # 5. 构建 OneBot 请求
        echo = f"upload_{target_type}_{target_id}_{int(time.time())}"
        abs_path = str(file_path.resolve())  # 绝对路径

        if target_type == "private":
            action = "upload_private_file"
            params = {
                "user_id": int(target_id),
                "file": abs_path,
                "name": safe_filename,
                "upload_file": True
            }
        elif target_type == "group":
            action = "upload_group_file"
            params = {
                "group_id": int(target_id),
                "file": abs_path,
                "name": safe_filename,
                "upload_file": True
            }
        else:
            return f"【上传失败】不支持的目标类型: {target_type}"

        request = {
            "action": action,
            "params": params,
            "echo": echo
        }

        # 6. 建立独立 WebSocket 连接并发送请求，等待响应
        try:
            async with websockets.connect(ws_url) as ws:
                await ws.send(json.dumps(request))

                # 等待匹配的响应，超时 30 秒
                start_time = time.time()
                timeout = 30
                while time.time() - start_time < timeout:
                    try:
                        response = await asyncio.wait_for(ws.recv(), timeout=1)
                        resp_data = json.loads(response)
                        if resp_data.get("echo") == echo:
                            # 匹配到我们的响应
                            if resp_data.get("status") == "ok":
                                # 上传成功，删除本地缓存文件
                                loop = asyncio.get_event_loop()
                                await loop.run_in_executor(None, _delete_file_sync, file_path)
                                return f"【上传成功】文件 {safe_filename} 已发送到聊天并删除本地缓存"
                            else:
                                error_msg = resp_data.get("message", "未知错误")
                                return f"【上传失败】{error_msg}"
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        return f"【上传失败】接收响应异常: {str(e)}"
                return f"【上传失败】等待 NapCat 响应超时（30秒）"
        except Exception as e:
            return f"【上传失败】WebSocket 连接或发送失败: {str(e)}"

    except Exception as e:
        return f"【上传失败】{str(e)}"

# ==================== 工具注册映射 ====================
TOOL_HANDLERS = {
    "upload_cached_file": upload_cached_file
}