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
UPX_LEVEL="${UPX_LEVEL:---best --lzma}"
BUILD_DIR="${BUILD_DIR:-}"
# 额外构建标签，附加在默认 netgo 之后（如 swagger 用于嵌入 swagger 文档）
EXTRA_BUILD_TAGS="${EXTRA_BUILD_TAGS:-}"
# 调用 go 的命令，默认就是 go 本身
# 有些仓库必须通过包装器调 go（比如用 go.work 管私有公共库、或要强制锁定依赖口径），
# 以前这类仓库只能整份复制一个构建脚本，连 ldflags、trimpath、UPX、buildinfo 都得照抄一遍
# 给一个 --go-cmd，它们只需写十来行设置环境变量再委托回本脚本
GO_CMD="${GO_CMD:-go}"

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
        --upx-level)
            UPX_LEVEL="$2"
            shift 2
            ;;
        --build-dir)
            BUILD_DIR="$2"
            shift 2
            ;;
        --build-tags)
            EXTRA_BUILD_TAGS="$2"
            shift 2
            ;;
        --go-cmd)
            GO_CMD="$2"
            shift 2
            ;;
        *)
            echo "未知参数: $1"
            echo "用法: $0 [--version VERSION] [--build-time TIME] [--git-commit COMMIT] [--output-dir DIR] [--binary-name NAME] [--os OS] [--arch ARCH] [--batch] [--build-tags TAGS] [--upx-compress true|false] [--upx-level LEVEL] [--build-dir DIR] [--go-cmd CMD]"
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
# 附加额外构建标签（如 swagger，仅 access-control 等需要嵌入 swagger 文档的服务启用）
if [[ -n "${EXTRA_BUILD_TAGS}" ]]; then
    BUILD_TAGS="${BUILD_TAGS} ${EXTRA_BUILD_TAGS}"
fi
TRIM_PATH="-trimpath"  # 移除文件路径信息
BUILDVCS="-buildvcs=false"  # CI 环境无需 VCS 信息，跳过探测提速

# 写构建信息文件
#
# 为什么要有这个文件：排查线上问题时第一个问题总是「这个 Pod 跑的是哪次构建」
# 很多服务的 main 包没有 version/buildTime/gitCommit 变量，-X 注入会被链接器静默忽略，
# 光看二进制看不出来所以把这几项另存一份纯文本，跟二进制一起进产物、一起进镜像，
# 进容器 cat 一下就知道
#
# 单平台构建写 buildinfo.txt；批量模式按平台分开写，避免互相覆盖
write_buildinfo() {
    local os=$1
    local arch=$2
    local binary_path=$3
    local info_path="${OUTPUT_DIR}/buildinfo.txt"

    if [[ "${BATCH_MODE}" == "true" ]]; then
        info_path="${OUTPUT_DIR}/buildinfo-${os}-${arch}.txt"
    fi

    local size=""
    if [[ -f "${binary_path}" ]]; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            size=$(ls -lh "${binary_path}" | awk '{print $5}')
        else
            size=$(du -h "${binary_path}" | cut -f1)
        fi
    fi

    cat > "${info_path}" <<INFO
binary=$(basename "${binary_path}")
version=${VERSION}
git_commit=${GIT_COMMIT}
build_time=${BUILD_TIME}
platform=${os}/${arch}
build_tags=${BUILD_TAGS}
go_cmd=${GO_CMD}
size=${size}
INFO
    echo "📝 构建信息: ${info_path}"
}

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
    echo "   - Go Command: ${GO_CMD}"
    
    BUILD_TARGET="${BUILD_DIR:-.}"
    # shellcheck disable=SC2086
    if GOOS=${os} GOARCH=${arch} CGO_ENABLED=0 ${GO_CMD} build \
        ${TRIM_PATH} ${BUILDVCS} \
        -tags "${BUILD_TAGS}" \
        -ldflags "${LDFLAGS}" \
        -o ${output} ${BUILD_TARGET}; then
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
            upx ${UPX_LEVEL} ${output} 2>/dev/null || upx --best ${output}
            if [[ "$OSTYPE" == "darwin"* ]]; then
                compressed_size=$(ls -lh ${output} | awk '{print $5}')
            else
                compressed_size=$(du -h ${output} | cut -f1)
            fi
            echo "📦 压缩后大小: ${compressed_size}"
        fi

        write_buildinfo "${os}" "${arch}" "${output}"
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
