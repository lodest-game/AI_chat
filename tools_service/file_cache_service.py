#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
file_cache_service.py - 文件缓存工具
将 AI 生成的内容缓存到本地文件，按 chat_id 隔离
"""

import os
import asyncio
from pathlib import Path
import re

# ==================== 工具定义 ====================
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "cache_generated_file",
            "description": "将生成的文件内容（如代码、文本）缓存到本地，按对话隔离",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "对话ID，用于隔离存储目录，例如 'qq_group_123456'"
                    },
                    "filename": {
                        "type": "string",
                        "description": "文件名（包含扩展名，如 'main.py'），将作为缓存文件名"
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容（文本格式）"
                    }
                },
                "required": ["chat_id", "filename", "content"]
            }
        }
    }
]

# ==================== 工具配置 ====================
TOOL_CONFIGS = {
    "cache_generated_file": {
        "timeout": 30.0,      # 超时时间
        "max_retries": 1,
        "enabled": True
    }
}

# ==================== 工具处理函数 ====================
async def cache_generated_file(chat_id: str, filename: str, content: str) -> str:
    """
    将生成的文件内容缓存到本地
    """
    try:
        # 1. 验证 chat_id 格式（可选，只允许字母、数字、下划线、横线、点）
        if not re.match(r'^[a-zA-Z0-9_\-\.]+$', chat_id):
            return f"【缓存失败】无效的 chat_id 格式: {chat_id}"

        # 2. 安全处理文件名：只保留基本名称，移除路径分隔符
        safe_filename = Path(filename).name
        if safe_filename != filename:
            # 说明 filename 包含了路径信息，拒绝
            return f"【缓存失败】文件名不能包含路径分隔符: {filename}"

        # 3. 构建存储目录：工具所在目录下的 cached_files/<chat_id>/
        base_dir = Path(__file__).parent / "cached_files" / chat_id
        base_dir.mkdir(parents=True, exist_ok=True)

        # 4. 写入文件
        file_path = base_dir / safe_filename
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _write_file_sync, file_path, content)

        # 5. 返回成功信息
        return f"【缓存成功】文件已保存至本地：{safe_filename} (对话：{chat_id})"

    except Exception as e:
        return f"【缓存失败】{str(e)}"

def _write_file_sync(file_path: Path, content: str):
    """同步写入文件（在 executor 中执行）"""
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

# ==================== 工具注册映射 ====================
TOOL_HANDLERS = {
    "cache_generated_file": cache_generated_file
}