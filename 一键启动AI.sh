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