#!/bin/bash

# SQLBot 快速启动脚本

set -e

echo "🚀 启动 SQLBot 后端服务..."

# 检查 Python 版本
python_version=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
if [ "$python_version" != "3.11" ] && [ "$python_version" != "3.12" ]; then
    echo "⚠️  警告: 需要 Python 3.11 或 3.12，当前版本: $python_version"
    echo "   建议使用 Python 3.11 或 3.12 运行项目"
fi

# 检查是否在虚拟环境中
if [ -z "$VIRTUAL_ENV" ]; then
    echo "⚠️  建议在虚拟环境中运行"
    echo "   创建虚拟环境: python3 -m venv venv"
    echo "   激活虚拟环境: source venv/bin/activate"
fi

# 检查数据库连接（可选）
echo "📊 检查配置..."

# 创建必要的目录
echo "📁 创建必要的目录..."
mkdir -p logs
mkdir -p data/file 2>/dev/null || true
mkdir -p data/excel 2>/dev/null || true
mkdir -p images 2>/dev/null || true
mkdir -p models 2>/dev/null || true

# 检查 .env 文件
if [ ! -f "../.env" ]; then
    echo "⚠️  未找到 .env 文件，使用默认配置"
    echo "   建议在项目根目录创建 .env 文件"
fi

# 启动应用
echo "✅ 启动应用..."
echo "📍 API 地址: http://localhost:8000"
echo "📍 API 文档: http://localhost:8000/docs"
echo ""
echo "按 Ctrl+C 停止服务"
echo ""

python main.py

