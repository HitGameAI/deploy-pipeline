#!/bin/bash
set -e

# ============================================
# 前端构建脚本 - 支持 npm、pnpm、yarn 三种包管理器
# 用途：自动化前端项目构建，支持版本注入和多种包管理器
# ============================================

# 初始化变量
VERSION=""                  # 构建版本号
BUILD_TIME=""               # 构建时间戳
GIT_COMMIT=""               # Git commit hash
OUTPUT_DIR="dist"           # 构建输出目录
NODE_VERSION=""             # Node.js 版本
PACKAGE_REGISTRY="https://registry.npmmirror.com"  # 包管理器仓库地址
PACKAGE_MANAGER="npm"       # 包管理器类型：npm、pnpm、yarn
BUILD_COMMAND=""            # 自定义构建命令（为空则使用包管理器默认 build 命令）

# ============================================
# 显示帮助信息
# ============================================
usage() {
    echo "前端构建脚本 - 支持 npm、pnpm、yarn"
    echo ""
    echo "使用方法: $0 [选项]"
    echo ""
    echo "必选参数:"
    echo "  --version           构建版本号 (必填)"
    echo "  --build-time        构建时间戳 (必填)"
    echo "  --git-commit        Git commit hash (必填)"
    echo "  --node-version      Node.js 版本 (必填)"
    echo ""
    echo "可选参数:"
    echo "  --output-dir        构建输出目录 (默认: dist)"
    echo "  --package-registry  包管理器仓库地址 (默认: https://registry.npmmirror.com)"
    echo "  --package-manager   包管理器类型: npm、pnpm 或 yarn (默认: npm)"
    echo "  --build-command     自定义构建命令 (如: pnpm run build, 为空则使用默认)"
    echo "  -h, --help          显示此帮助信息"
    exit 1
}

# ============================================
# 解析命令行参数
# ============================================
while [[ $# -gt 0 ]]; do
    case "$1" in
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
        --node-version)
            NODE_VERSION="$2"
            shift 2
            ;;
        --package-registry)
            PACKAGE_REGISTRY="$2"
            shift 2
            ;;
        --package-manager)
            PACKAGE_MANAGER="$2"
            shift 2
            ;;
        --build-command)
            BUILD_COMMAND="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "错误: 未知选项: $1"
            usage
            ;;
    esac
done

# ============================================
# 参数验证
# ============================================

# 检查必填参数
if [[ -z "$VERSION" || -z "$BUILD_TIME" || -z "$GIT_COMMIT" || -z "$NODE_VERSION" ]]; then
    echo "错误: 缺少必填参数"
    usage
fi

# 验证包管理器类型
case "$PACKAGE_MANAGER" in
    npm|pnpm|yarn)
        ;;
    *)
        echo "错误: 无效的包管理器 '$PACKAGE_MANAGER'，必须是 npm、pnpm 或 yarn"
        exit 1
        ;;
esac

# ============================================
# 显示构建配置信息
# ============================================
echo "================================================"
echo "           前端构建配置信息"
echo "================================================"
echo "版本号:             $VERSION"
echo "构建时间:           $BUILD_TIME"
echo "Git Commit:         $GIT_COMMIT"
echo "输出目录:           $OUTPUT_DIR"
echo "Node.js 版本:       $NODE_VERSION"
echo "包管理器仓库:       $PACKAGE_REGISTRY"
echo "包管理器类型:       $PACKAGE_MANAGER"
echo "构建命令:           ${BUILD_COMMAND:-默认 (npm/pnpm/yarn run build)}"
echo "================================================"

# ============================================
# 配置包管理器仓库
# ============================================
echo ""
echo "正在配置包管理器仓库..."
case "$PACKAGE_MANAGER" in
    npm)
        npm config set registry "$PACKAGE_REGISTRY"
        ;;
    pnpm)
        pnpm config set registry "$PACKAGE_REGISTRY"
        ;;
    yarn)
        yarn config set registry "$PACKAGE_REGISTRY"
        ;;
esac

# ============================================
# 安装依赖
# ============================================
echo ""
echo "正在安装依赖..."
case "$PACKAGE_MANAGER" in
    npm)
        if [[ -f package-lock.json ]]; then
            echo "使用 npm ci 安装依赖..."
            npm ci --prefer-offline --no-audit
        else
            echo "使用 npm install 安装依赖..."
            npm install --prefer-offline --no-audit
        fi
        ;;
    pnpm)
        if [[ -f pnpm-lock.yaml ]]; then
            echo "使用 pnpm install --frozen-lockfile 安装依赖..."
            pnpm install --prefer-offline --frozen-lockfile
        else
            echo "使用 pnpm install 安装依赖..."
            pnpm install --prefer-offline
        fi
        ;;
    yarn)
        if [[ -f yarn.lock ]]; then
            echo "使用 yarn install --frozen-lockfile 安装依赖..."
            yarn install --frozen-lockfile
        else
            echo "使用 yarn install 安装依赖..."
            yarn install
        fi
        ;;
esac

# ============================================
# 构建前端项目
# ============================================
echo ""
echo "正在构建前端项目..."

# 设置环境变量，注入版本信息到前端应用
export VUE_APP_VERSION="$VERSION"
export VUE_APP_BUILD_TIME="$BUILD_TIME"
export VUE_APP_GIT_COMMIT="$GIT_COMMIT"
export REACT_APP_VERSION="$VERSION"
export REACT_APP_BUILD_TIME="$BUILD_TIME"
export REACT_APP_GIT_COMMIT="$GIT_COMMIT"

# 执行构建命令
if [[ -n "$BUILD_COMMAND" ]]; then
    echo "使用自定义构建命令: $BUILD_COMMAND"
    if $BUILD_COMMAND 2>&1; then
        echo "构建成功！"
    else
        echo "构建失败！"
        exit 1
    fi
else
    case "$PACKAGE_MANAGER" in
        npm)
            if npm run build 2>&1; then
                echo "构建成功！"
            else
                echo "构建失败！"
                exit 1
            fi
            ;;
        pnpm)
            if pnpm run build 2>&1; then
                echo "构建成功！"
            else
                echo "构建失败！"
                exit 1
            fi
            ;;
        yarn)
            if yarn build 2>&1; then
                echo "构建成功！"
            else
                echo "构建失败！"
                exit 1
            fi
            ;;
    esac
fi

# ============================================
# 验证构建输出
# ============================================
echo ""
echo "正在验证构建输出..."
if [[ -d "$OUTPUT_DIR" ]]; then
    echo "输出目录存在: $OUTPUT_DIR"
    echo "输出文件列表:"
    ls -la "$OUTPUT_DIR"
else
    echo "错误: 输出目录 $OUTPUT_DIR 不存在！"
    exit 1
fi

# ============================================
# 构建完成
# ============================================
echo ""
echo "================================================"
echo "           前端构建完成"
echo "================================================"