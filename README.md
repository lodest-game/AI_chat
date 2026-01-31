# 提示：模型建议采用8B-Q4以上，4B模型容易遇到睿智调用问题。默认采用qwen-vl识图模型，如果仅需LLM，请自行修改配置文件。
# 警告：我并不会编程，也不是有关编程职业，只是一条社会蛆虫，我只提出了具体并且完善的逻辑和思路，所有的代码实现，都是由AI实现的。
# 警告：请勿进行人身攻击，我也不完全会使用GitHub，如果你提交一些申请，我大概率不知道怎么操作，导致不会回复等，最后的最后，这个项目的维护全看运气，万一我就搞懂了呢？万一呢？
# 总感觉，好像readme有什么重要的信息忘记写了，是什么呢？之前还能想到的，忽然想不起来了，淦！

# 异步AI代理系统 (Asynchronous AI Agent System)

一个基于`asyncio`的完全异步AI代理系统，支持多客户端（QQ机器人等）/服务端（LMStudio等）对接、多模态处理和工具调用，提供高并发、模块化的AI服务架构。

## ✨ 核心特性

### 🚀 异步架构
- **完全异步设计**: 基于`asyncio`的异步架构，支持高并发处理
- **混合并发模型**: 异步IO + 多线程转码的图片处理
- **严格顺序保证**: 基于`asyncio.Queue`的顺序消息处理

### 🧠 多模态支持
- **图片智能处理**: 自动识别并转换图片URL为base64格式
- **多模态对话**: 支持文本、图片混合消息处理
- **智能缓存**: 图片缓存系统，支持特权对话配置

### 🛠️ 工具生态系统
- **动态工具发现**: 自动扫描并注册工具函数
- **工具调用循环**: 支持多轮工具调用交互
- **异步工具执行**: 所有工具均为异步函数设计

### 🔧 模块化设计
- **插件化架构**: 所有模块均可独立扩展
- **配置驱动**: 统一的JSON配置管理系统
- **热重载支持**: 动态加载工具和配置

## 📋 系统要求

- **Python**: 3.8 或更高版本
- **操作系统**: Windows / Linux / macOS
- **内存**: 建议至少 2GB RAM
- **磁盘空间**: 至少 100MB 可用空间

## 🚀 快速开始

### 1. 克隆仓库
```bash
git clone https://github.com/lodest-game/AI_chat.git
cd async-ai-agent
```

### 2. 安装依赖
使用虚拟环境（推荐）
```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# 安装核心依赖
pip install -r requirements.txt
```

### 3. 基础配置
系统会自动创建默认配置。如需自定义，请编辑：
```
plugins/
├── system.json          # 系统配置文件
└── image_config.json    # 图片管理器配置
```

### 4. 运行系统
你自己写个虚拟环境下运行的脚本吧，我懒，以下只是一个AI示例，建议按照自己的需求改:

```
#!/bin/bash
# agent_start.sh - AI Agent系统启动脚本

# 设置颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 显示欢迎信息
show_welcome() {
    echo -e "${GREEN}🤖 AI Agent 系统启动器${NC}"
    echo "========================================"
    echo "脚本目录: $(pwd)"
    echo "========================================"
}

# 主函数
main() {
    show_welcome

    # 获取脚本目录
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    cd "$SCRIPT_DIR"

    # 检查Python
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}错误: 未找到 python3${NC}"
        echo "请安装Python3: sudo apt install python3 python3-venv"
        echo "按任意键退出..."
        read -n 1
        exit 1
    fi

    # 检查主程序
    if [ ! -f "Agent_core.py" ]; then
        echo -e "${RED}错误: 未找到 Agent_core.py${NC}"
        echo "请确保脚本与 Agent_core.py 在同一目录"
        echo "按任意键退出..."
        read -n 1
        exit 1
    fi

    # 检查是否需要重新创建虚拟环境
    FORCE_RECREATE=false
    if [ "$1" = "--force" ] || [ "$1" = "-f" ]; then
        FORCE_RECREATE=true
        echo -e "${YELLOW}强制重新创建虚拟环境...${NC}"
    fi

    # 检查虚拟环境是否存在且有效
    VENV_EXISTS=false
    if [ -d "venv" ]; then
        if [ -f "venv/bin/activate" ] && [ -d "venv/lib" ]; then
            VENV_EXISTS=true
            echo -e "${GREEN}找到现有的虚拟环境${NC}"
        else
            echo -e "${YELLOW}虚拟环境不完整，需要重新创建${NC}"
            FORCE_RECREATE=true
        fi
    fi

    # 检查依赖是否已安装
    DEPS_INSTALLED=false
    if [ "$VENV_EXISTS" = true ]; then
        source venv/bin/activate
        if python -c "import aiohttp, psutil, yaml, aiofiles" &> /dev/null; then
            DEPS_INSTALLED=true
            echo -e "${GREEN}依赖检查通过${NC}"
        else
            echo -e "${YELLOW}依赖不完整，需要重新安装${NC}"
            FORCE_RECREATE=true
        fi
        deactivate
    fi

    # 删除并重新创建虚拟环境（如果需要）
    if [ "$FORCE_RECREATE" = true ] && [ -d "venv" ]; then
        echo -e "${YELLOW}删除旧的虚拟环境...${NC}"
        rm -rf venv
        VENV_EXISTS=false
        DEPS_INSTALLED=false
    fi

    # 创建新的虚拟环境（如果不存在）
    if [ "$VENV_EXISTS" = false ]; then
        echo -e "${YELLOW}创建新的虚拟环境...${NC}"
        python3 -m venv venv
        if [ $? -ne 0 ]; then
            echo -e "${RED}创建虚拟环境失败${NC}"
            echo "请安装: sudo apt install python3-venv"
            echo "按任意键退出..."
            read -n 1
            exit 1
        fi
    fi

    # 激活虚拟环境
    echo -e "${YELLOW}激活虚拟环境...${NC}"
    source venv/bin/activate

    # 安装或更新依赖（仅在需要时）
    if [ "$DEPS_INSTALLED" = false ]; then
        echo -e "${YELLOW}安装依赖...${NC}"
        pip install --upgrade pip
        
        # 安装核心依赖
        pip install aiohttp>=3.8.0
        pip install psutil>=5.9.0
        pip install PyYAML>=6.0
        pip install aiofiles>=23.2.0
        pip install websockets>=12.0
        
        # 安装可选的依赖（如果可用）
        pip install importlib-metadata>=4.0 || echo "跳过importlib-metadata"
        
        if [ $? -ne 0 ]; then
            echo -e "${RED}依赖安装失败${NC}"
            echo "按任意键退出..."
            read -n 1
            exit 1
        fi
        
        echo -e "${GREEN}依赖安装完成${NC}"
    else
        echo -e "${GREEN}使用现有依赖${NC}"
    fi

    # 快速验证安装
    echo -e "${YELLOW}验证环境...${NC}"
    if python -c "import aiohttp, psutil, yaml, aiofiles; print('✅ 环境验证通过')"; then
        echo -e "${GREEN}环境验证成功${NC}"
    else
        echo -e "${RED}环境验证失败${NC}"
        echo "按任意键退出..."
        read -n 1
        exit 1
    fi

    # 检查是否已在运行
    if pgrep -f "python.*Agent_core.py" > /dev/null; then
        echo -e "${YELLOW}检测到程序已在运行，先停止...${NC}"
        pkill -f "python.*Agent_core.py"
        sleep 2
    fi

    # 启动程序
    echo -e "${GREEN}启动AI Agent系统...${NC}"
    echo "========================================"
    python Agent_core.py

    # 程序退出后的处理
    echo ""
    echo -e "${YELLOW}程序已退出${NC}"
    deactivate
    echo "按任意键关闭窗口..."
    read -n 1
}

# 捕获Ctrl+C信号
trap 'echo -e "\n${YELLOW}用户中断执行${NC}"; deactivate 2>/dev/null; exit 1' INT

# 运行主函数
main "$@"
```

## 📁 项目结构

```
async-ai-agent/
├── Agent_core.py              # 系统主入口
├── plugins/                   # 核心模块
│   ├── config_manager.py     # 配置管理
│   ├── context_manager.py    # 上下文管理
│   ├── queue_manager.py      # 异步队列
│   ├── task_manager.py       # 任务调度
│   ├── rules_manager.py      # 规则管理
│   ├── session_manager.py    # 会话管理
│   ├── tool_manager.py       # 工具管理
│   ├── essentials_manager.py # 指令处理
│   ├── port_manager.py       # 端口管理
│   └── image_manager.py      # 图片处理
├── clients/                  # 客户端模块
├── models/                   # 模型服务
├── tools_service/           # 工具函数
├── chat/                    # 对话数据
│   └── history/            # 历史记录
├── logs/                    # 系统日志
└── README.md               # 本文档
```

## 🔌 模块详解

### 核心模块

1. **配置管理器 (ConfigManager)**
   - 统一配置管理
   - 配置验证和默认值
   - 热重载支持

2. **上下文管理器 (ContextManager)**
   - 异步对话历史管理
   - 智能消息修剪
   - 虚拟回复支持

3. **图片管理器 (ImageManager)**
   - 混合架构图片处理
   - 智能URL转base64
   - 缓存和并发控制

4. **工具管理器 (ToolManager)**
   - 动态工具注册
   - 异步工具执行
   - OpenAI格式兼容

### 工作流程
```
客户端消息 → 队列管理器 → 任务调度器 → 
├→ 工作流A (指令处理)
├→ 工作流B (会话准备)
└→ 工作流C (模型处理+工具调用)
```

## ⚙️ 配置说明

### 系统配置 (plugins/system.json)
你需要根据自己的本地模型或者服务商提供的模型的类型，决定模型是否属于LLM或者MLLM。
```json
{
  "system": {
    "context_manager": {
      "default_model": "local_model", # 默认模型
      "chat_mode": { # 模型列表
        "LLM": [],
        "MLLM": [
          "local_model" # 默认模型属于MLLM-多模态模型（识图类）
        ]
      },
      "default_tools_call": true, # 默认模型工具调用启用
      "model": {
        "max_tokens": 64000, # 限制模型最大上下文token
        "temperature": 0.7, # 是否允许模型输出内容更自由化，值越大，幻觉越大，信息越是拟人，回复越是跳跃
        "stream": false # 是否启用模型流式输出，注意：除非你的前端支持流式输出，否则请勿修改（流式输出理论上会导致会话被清除，无法正常输出）
      },
      "core_prompt": [ # 默认核心提示词（专属提示词不在此处配置，仅配置核心的基础提示词，用于系统级别的安全限制）
        "你是一个即时聊天的参与者。在群聊中存在多个用户会同时发言。",
        "【自主决策指南】",
        "作为自主即时聊天参与者，请遵循以下决策原则：",
        "1. 工具调用判断：只有在用户明确要求工具相关操作时调用工具",
        "2. 总结类请求：用户要求总结时，专注分析对话历史即可，无需外部工具",
        "3. 决策信心度：如果不确定是否需要工具，优先选择不调用",
        "4. 意图验证：如果用户意图模糊，可通过对话澄清，而非直接调用工具",
        "你所处的环境是即时聊天平台，请关注当前问题，历史问题作为聊天背景，面对即时聊天，存在大量无效噪音，请过滤无效信息后回答。",
        "接受的信息格式中，'发言人'表示发言人昵称。'发言内容'表示具体的用户讨论信息。",
        "消息格式中'发言人'表示发言者身份，例如'发言人：腾讯网'表示'腾讯网'这个用户说的话。",
        "请以自然、流畅的方式参与对话，直接回应当前用户的问题或评论。",
        "不需要复述完整的用户发言内容，只需针对性地回复。",
        "对于群聊中的多人讨论，可以自然地引用或回应特定用户。",
        "对于相关工具定义不存在的功能请求，请告知用户无法做到，而不是使用虚拟的回应。",
        "保持对话连贯，避免机械化地重复格式信息。"
      ],
      "max_user_messages_per_chat": 100, # 单个会话记录的上下文数量（仅保存多少轮用户发言）
      "cache_inactive_unload_seconds": 1800 # 单个会话长时间不活跃自动卸载缓存时间配置
    },
    "rules_manager": {
      "mode": "wait" # wait：同一会话的请求串行，不同会话的请求并行，all：所有请求并行。注意：需要后端LLM框架（VLLM等）支持并行策略
    },
    "port_manager": {
      "reconnect_interval": 10, # 对接客户端/服务端，断线自动重连间隔（s）
      "max_reconnect_attempts": 3 # 最大尝试重连次数
    },
    "essentials_manager": {
      "enable_model_management": true, # 是否允许通过会话基础指令更换指定会话模型
      "enable_prompt_management": true, # 是否允许通过会话基础指令更换指定会话提示词
      "enable_tool_management": true, # 是否允许通过会话基础指令重载工具注册信息（用于热加载工具）
      "admin_chats": [
        "qq_private_1308213863" # 基础指令重载工具权限的指定会话id（只有此会话id才能使用工具重载指令）
      ]
    },
    "session_manager": {
      "session_timeout_minutes": 5, # 临时会话不活跃超时设置（用于强制清理失效的临时会话，理论上应该配合服务端接口模块使用，目前的服务端接口没有超时清理功能，采用的aiohttp默认超时链接管理300s，如果你需要修改此配置，需要接口端模块支持超时清理，并同步设置会话清理时间。）
      "max_sessions": 100 # 临时会话最大存在数量（某种意义上来说，这就是代表同时会话请求的并发量，允许AI同时处理多少个请求的最大值）
    }
  }
}
```

### 图片配置 (plugins/image_config.json)
```json
{
  "cache": {
    "default_ttl_seconds": 60, # 默认对话缓存图片信息时间（s）
    "privilege_ttl_seconds": 1800, # 指定对话缓存图片信息时间（s）
    "default_max_per_chat": 10, # 默认对话缓存图片数量
    "privilege_max_per_chat": 20 # 指定对话缓存图片数量
  },
  "concurrency": {
    "max_concurrent_downloads": 8, # 最大图片并发下载数量（需要根据）
    "max_encoding_threads": 4, # 最大图片转码线程数量
    "download_timeout": 30 # 图片下载超时设定
  },
  "privilege": [] # 指定会话id
}
```

## 💡 使用示例

### 基础指令
系统支持以下基础指令：
- `#模型列表` - 查看可用模型
- `#模型更换 <模型名>` - 更换当前模型
- `#工具支持 <true/false>` - 启用/禁用工具
- `#提示词` - 查看/设定/删除专属提示词
- `#上下文清理` - 清理对话历史
- `#帮助` - 查看帮助信息

### 基础指令
系统自带工具函数：
- `#提示词` - 查看/设定/删除专属提示词

### 自定义工具开发
暂时没想好怎么写啊...
总而言之，言而总之，工具业务可以通过向系统申请chat_id（会话id）请求，来做到指定会话下，独立配置工具业务权限的设置。
工具业务返回的信息对系统而言是完善的一个content数据：
```json
{
  "role": "tool",
  "tool_call_id": "系统内处理匹配",
  "name": "系统内处理",
  "content": "这是你开发的第三方业务的实际数据",
}
```

### 扩展工具函数
1. 在`tools_service/`目录添加`.py`文件（我提交了一个百度AI搜索的业务，可以参考一下，当然，没有提供对应的密钥，如果你问，安装后为什么不能联网，那我只能说，你给钱吗？不给钱你说尼玛呢。）
2. 实现异步函数，包含完整文档字符串
3. 系统会自动注册并生成OpenAI格式定义

## 🎯 性能特点

### 并发处理
- **消息队列**: 每个对话独立队列，保证顺序
- **图片处理**: 异步下载 + 多线程转码
- **工具执行**: 完全异步，支持并发调用

### 内存管理
- **智能缓存**: LRU缓存策略，自动清理
- **会话管理**: 超时会话自动回收
- **资源控制**: 可配置的最大并发数

## 🔧 开发指南

### 添加新客户端
1. 在`clients/`目录创建`xxx_client.py`
2. 实现`Client`类，包含异步方法：
   ```python
   class Client:
       async def start(self, config, message_callback):
           pass
       
       async def send_message_async(self, data):
           pass
   ```
3. 客户端协议
客户端需要实现以下接口：

3-1. 消息接收格式
客户端收到消息后，应转换为以下格式传递给系统：

```json
{
  "chat_id": "平台_类型_ID",          // 如: qq_private_123456
  "content": "消息内容或消息数组",    // 文本或OpenAI格式的多模态消息
  "user_id": "发送者ID",             // 可选
  "group_id": "群组ID",             // 可选，群聊时使用
  "message_type": "private|group",   // 消息类型
  "is_respond": true|false,          // 是否需要AI响应
  "timestamp": 1234567890.123       // Unix时间戳
}
```

3-2. 消息内容格式
多模态消息（OpenAI格式）：

```json
[
  {
    "type": "text",
    "text": "这是一段文本"
  },
  {
    "type": "image_url",
    "image_url": {
      "url": "http://example.com/image.jpg"
    }
  }
]
```

3-3. 客户端接口要求
客户端类必须实现以下方法：

```python
class Client:
    async def start(self, config: dict, message_callback: callable):
        """启动客户端
        Args:
            config: 客户端配置
            message_callback: 消息回调函数，用于将接收到的消息传递给系统
        """
        
    async def send_message_async(self, response_data: dict):
        """发送消息到客户端
        Args:
            response_data: 响应数据，格式见下文
        """
        
    async def is_connected_async(self) -> bool:
        """检查连接状态"""
        
    async def stop(self):
        """停止客户端"""
```

3-4. 响应数据格式
系统发送给客户端的响应格式：

```json
{
  "chat_id": "qq_private_123456",
  "content": "AI回复内容",
  "timestamp": 1234567890.123
}
```

### 添加新模型服务
1. 在`models/`目录创建`xxx_model.py`
2. 实现`Model`类，包含异步方法：
   ```python
   class Model:
       async def start(self, config):
           pass
       
       async def send_request_async(self, data):
           pass
   ```
3. 服务端协议
模型服务端需要实现以下接口：

3-1. 请求数据格式（理论上你也可以不改，完全仅作为中转服务，系统层面已经完成了对应的协议格式）
系统发送给模型服务的请求格式：

```json
{
  "chat_id": "对话ID",
  "session_data": {
    "model": "模型名称",
    "messages": [
      {"role": "system", "content": "系统提示词"},
      {"role": "user", "content": "用户消息"}
    ],
    "tools": [],          // 工具定义（可选）
    "max_tokens": 64000,
    "temperature": 0.7
  },
  "timestamp": 1234567890.123
}
```

3-2. 响应数据格式
模型服务返回的响应格式（OpenAI API兼容）：

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "AI回复内容",
        "tool_calls": []  // 工具调用请求（可选）
      }
    }
  ],
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 50
  }
}
```

3-3. 服务端接口要求
模型服务类必须实现以下方法：

```python
class Model:
    async def start(self, config: dict):
        """启动模型服务
        Args:
            config: 模型配置
        """
        
    async def send_request_async(self, request_data: dict) -> dict:
        """处理模型请求
        Args:
            request_data: 请求数据
        Returns:
            模型响应
        """
        
    async def is_connected_async(self) -> bool:
        """检查连接状态"""
        
    async def stop(self):
        """停止模型服务"""
```

## 📊 监控和日志

### 系统状态
系统提供多种状态查询：
- 队列状态：`queue_manager.get_queue_status()`
- 缓存状态：`image_manager.get_cache_status()`
- 会话状态：`session_manager.get_status()`

### 日志文件
- 主日志：`logs/agent_core.log`
- 模块日志：各模块独立日志记录
- 错误追踪：完整的异常堆栈记录

## 🤝 贡献指南

欢迎贡献！请遵循以下步骤：

以下是deepseek老师的建议，我反正看不懂，我也用的少，但是就很震撼。
1. **Fork 仓库**
2. **创建功能分支** (`git checkout -b feature/AmazingFeature`)
3. **提交更改** (`git commit -m 'Add some AmazingFeature'`)
4. **推送到分支** (`git push origin feature/AmazingFeature`)
5. **开启 Pull Request**

### 开发规范
- 异步优先：建议使用异步实现

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 📞 支持与反馈

- **问题报告**: [GitHub Issues]
- **功能请求**: 通过Issues提交
- **讨论区**: 没有啊，我不知道怎么写readme啊，那你能帮帮我吗？

## 🙏 致谢

感谢为这个项目做出贡献的我的脑细胞和Deepseek网页版！

---

**提示**: 首次运行时会自动创建必要的目录和配置文件。请确保有足够的磁盘空间和网络连接（用于图片下载）。

**注意**: 生产环境部署前请仔细审查安全配置，特别是权限和网络设置。






