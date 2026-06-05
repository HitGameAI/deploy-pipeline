#!/bin/bash
###
 # @Author: kamalyes 501893067@qq.com
 # @Date: 2025-11-29 10:56:54
 # @LastEditors: kamalyes 501893067@qq.com
 # @LastEditTime: 2026-01-13 20:15:08
 # @FilePath: \demo-core-service\scripts\build-linux.sh
 # @Description: Build script for Linux and macOS platforms with version info
 # 
 # Copyright (c) 2025 by kamalyes, All Rights Reserved. 
### 

set -e

# 默认值
VERSION="${VERSION:-dev}"
BUILD_TIME="${BUILD_TIME:-$(date -u '+%Y-%m-%d_%H:%M:%S')}"
GIT_COMMIT="${GIT_COMMIT:-unknown}"
OUTPUT_DIR="${OUTPUT_DIR:-./deployments}"
BINARY_NAME="${BINARY_NAME:-demo-core-service}"
BUILD_OS="${BUILD_OS:-linux}"
BUILD_ARCH="${BUILD_ARCH:-amd64}"
BATCH_MODE="${BATCH_MODE:-false}"
UPX_COMPRESS="${UPX_COMPRESS:-false}"

# 参数解析
while [[ $# -gt 0 ]]; do
    case $1 in
        --version)
            VERSION="$2"
            shift 2
            ;;
        --build-time)
            BUILD_TIME="$2"
            shift 2
            ;;
        --git-commit)
            GIT_COMMIT="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --binary-name)
            BINARY_NAME="$2"
            shift 2
            ;;
        --os)
            BUILD_OS="$2"
            shift 2
            ;;
        --arch)
            BUILD_ARCH="$2"
            shift 2
            ;;
        --batch)
            BATCH_MODE="true"
            shift
            ;;
        --upx-compress)
            UPX_COMPRESS="$2"
            shift 2
            ;;
        *)
            echo "未知参数: $1"
            echo "用法: $0 [--version VERSION] [--build-time TIME] [--git-commit COMMIT] [--output-dir DIR] [--binary-name NAME] [--os OS] [--arch ARCH] [--batch]"
            exit 1
            ;;
    esac
done

# 创建输出目录
mkdir -p "${OUTPUT_DIR}"

# 自动安装 UPX（如果需要且未安装）
install_upx() {
    if command -v upx &> /dev/null; then
        echo "✅ UPX 已安装: $(upx --version | head -1)"
        return 0
    fi
    
    echo "📥 UPX 未安装，正在自动安装..."
    
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        if command -v apt-get &> /dev/null; then
            sudo apt-get update && sudo apt-get install -y upx-ucl
        elif command -v yum &> /dev/null; then
            sudo yum install -y upx
        else
            echo "⚠️  无法自动安装 UPX，请手动安装: https://upx.github.io/"
            return 1
        fi
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        if command -v brew &> /dev/null; then
            brew install upx
        else
            echo "⚠️  请先安装 Homebrew，或手动安装 UPX: https://upx.github.io/"
            return 1
        fi
    elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
        # Windows (Git Bash)
        echo "🔽 下载 UPX for Windows..."
        UPX_VERSION="4.2.2"
        curl -L "https://github.com/upx/upx/releases/download/v${UPX_VERSION}/upx-${UPX_VERSION}-win64.zip" -o /tmp/upx.zip
        unzip -o /tmp/upx.zip -d /tmp/
        mkdir -p ~/bin
        cp /tmp/upx-${UPX_VERSION}-win64/upx.exe ~/bin/
        export PATH="$HOME/bin:$PATH"
        rm -rf /tmp/upx.zip /tmp/upx-${UPX_VERSION}-win64
        echo "✅ UPX 安装完成"
    else
        echo "⚠️  不支持的操作系统: $OSTYPE"
        return 1
    fi
    
    if command -v upx &> /dev/null; then
        echo "✅ UPX 安装成功: $(upx --version | head -1)"
        return 0
    else
        echo "❌ UPX 安装失败"
        return 1
    fi
}

# 如果启用了 UPX 压缩，先检查并尝试安装
if [[ "${UPX_COMPRESS}" == "true" ]]; then
    echo "🗜️  UPX 压缩已启用"
    if ! install_upx; then
        echo "⚠️  UPX 安装失败，将跳过压缩步骤"
        UPX_COMPRESS="false"
    fi
else
    echo "ℹ️  UPX 压缩已禁用（使用 --upx-compress true 启用）"
fi

# 定义批量构建目标平台
batch_targets=(
    "linux/amd64"
    "linux/386"
    "linux/arm64"
    "linux/arm"
    "darwin/amd64"
    "darwin/arm64"
)

# 构建 ldflags
# -s: 去除符号表 | -w: 去除调试信息 | -extldflags: 链接器参数
LDFLAGS="-s -w -extldflags '-static' -X main.version=${VERSION} -X main.buildTime=${BUILD_TIME} -X main.gitCommit=${GIT_COMMIT}"

# 构建选项
BUILD_TAGS="netgo"  # 使用纯 Go 网络实现，避免 cgo 依赖
TRIM_PATH="-trimpath"  # 移除文件路径信息

# 构建函数
build_target() {
    local os=$1
    local arch=$2
    local output="${OUTPUT_DIR}/${BINARY_NAME}"
    
    # 如果是批量模式，添加平台后缀
    if [[ "${BATCH_MODE}" == "true" ]]; then
        output="${OUTPUT_DIR}/${BINARY_NAME}-${os}-${arch}"
    fi
    
    echo "🚀 正在构建: ${output}"
    echo "📦 版本信息:"
    echo "   - Version: ${VERSION}"
    echo "   - Build Time: ${BUILD_TIME}"
    echo "   - Git Commit: ${GIT_COMMIT}"
    echo "   - Platform: ${os}/${arch}"
    
    if GOOS=${os} GOARCH=${arch} CGO_ENABLED=0 go build \
        ${TRIM_PATH} \
        -tags "${BUILD_TAGS}" \
        -ldflags "${LDFLAGS}" \
        -o ${output} main.go; then
        echo "✅ 构建成功: ${output}"
        
        # 显示文件大小
        if [[ "$OSTYPE" == "darwin"* ]]; then
            size=$(ls -lh ${output} | awk '{print $5}')
        else
            size=$(du -h ${output} | cut -f1)
        fi
        echo "📦 原始大小: ${size}"
        
        # 可选：使用 UPX 压缩（如果安装了 UPX）
        if command -v upx &> /dev/null && [[ "${UPX_COMPRESS}" == "true" ]]; then
            echo "🗜️  使用 UPX 压缩..."
            upx --best --lzma ${output} 2>/dev/null || upx --best ${output}
            if [[ "$OSTYPE" == "darwin"* ]]; then
                compressed_size=$(ls -lh ${output} | awk '{print $5}')
            else
                compressed_size=$(du -h ${output} | cut -f1)
            fi
            echo "📦 压缩后大小: ${compressed_size}"
        fi
    else
        echo "❌ 构建失败: ${output}"
        return 1
    fi
    echo ""
}

# 执行构建
if [[ "${BATCH_MODE}" == "true" ]]; then
    echo "🔄 批量构建模式..."
    for target in "${batch_targets[@]}"; do
        os=${target%/*}
        arch=${target#*/}
        build_target "$os" "$arch"
    done
    echo "🎉 批量构建完成！输出目录: ${OUTPUT_DIR}/"
    ls -la "${OUTPUT_DIR}/"
else
    echo "🔨 单平台构建模式..."
    build_target "${BUILD_OS}" "${BUILD_ARCH}"
    echo "🎉 构建完成！"
fi