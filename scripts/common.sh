#!/bin/bash
# 通用函数库 | Shared shell helpers

# shellcheck disable=SC2155
# 这里大量 local x=$(cmd) 是刻意的，不要拆成两行
#
# 本文件被 notify.sh 以 set -e 的方式 source，合写时命令的退出码被 local 吃掉、
# 函数继续往下走；拆成 local x 再 x=$(cmd) 之后，退出码就会生效，那一行直接中断
# 实测过这个差别：合写时函数跑完并拿到空值，分写时赋值那一行之后的语句根本不执行
#
# 被标记的那几处是 basename / printf / ls 管 awk，用在一个打印目录树的助手里
# 一个文件瞬时消失就中断整条通知，比打印出空的大小要糟得多
#
# [EN] The combined form is deliberate here and must not be split. This file is sourced by
# notify.sh under set -e: combined, the command's status is swallowed by local and the function
# carries on, whereas splitting makes that status effective and aborts at that line. The
# difference was verified. The sites flagged are basename, printf and ls piped to awk inside a
# directory-tree helper, where aborting a whole notification because a file vanished for a
# moment is far worse than printing an empty size

# 颜色常量 | Colour constants
# 只留用到的三个：黄、蓝、品红定义了却从没被引用过，留着只会让人以为有一套完整调色板
# [EN] Only the three in use are kept; yellow, blue and magenta were defined and never
# referenced, and leaving them suggests a full palette that is not actually there
COLOR_RESET="\033[0m"
COLOR_GREEN="\033[32m"
COLOR_CYAN="\033[36m"
COLOR_RED="\033[31m"

# 日志文件路径（可选）
LOG_FILE="${LOG_FILE:-/tmp/deploy-$(date +%Y%m%d-%H%M%S).log}"

# ==================== 基础日志函数 ====================
log_info() {
    echo "ℹ️  $1" | tee -a "${LOG_FILE}"
}

log_success() {
    echo "✅ $1" | tee -a "${LOG_FILE}"
}

log_error() {
    echo "❌ $1" | tee -a "${LOG_FILE}" >&2
}

log_warn() {
    echo "⚠️  $1" | tee -a "${LOG_FILE}"
}

log_section() {
    echo "" | tee -a "${LOG_FILE}"
    echo "================================================" | tee -a "${LOG_FILE}"
    echo "  $1" | tee -a "${LOG_FILE}"
    echo "================================================" | tee -a "${LOG_FILE}"
    echo "" | tee -a "${LOG_FILE}"
}

log_step() {
    echo "" | tee -a "${LOG_FILE}"
    echo "▶️  $1" | tee -a "${LOG_FILE}"
}

# ==================== 命令执行函数 ====================
run_command() {
    local command="$1"
    local description="${2:-执行命令}"
    
    # 使用带颜色的输出显示即将执行的命令
    echo -e "${COLOR_CYAN}🔧 ${description}${COLOR_RESET}" | tee -a "${LOG_FILE}"
    echo -e "${COLOR_GREEN}   $ ${command}${COLOR_RESET}" | tee -a "${LOG_FILE}"
    echo "" | tee -a "${LOG_FILE}"
    
    # 执行命令并捕获输出和错误
    if eval "$command" 2>&1 | tee -a "${LOG_FILE}"; then
        echo "" | tee -a "${LOG_FILE}"
        echo -e "${COLOR_GREEN}✅ 命令执行成功${COLOR_RESET}" | tee -a "${LOG_FILE}"
        return 0
    else
        echo "" | tee -a "${LOG_FILE}"
        echo -e "${COLOR_RED}❌ 命令执行失败: ${command}${COLOR_RESET}" | tee -a "${LOG_FILE}" >&2
        return 1
    fi
}

# ==================== 环境变量检查 ====================
check_env() {
    local required_vars=("$@")
    local missing_vars=()
    
    for var in "${required_vars[@]}"; do
        if [ -z "${!var}" ]; then
            missing_vars+=("$var")
        fi
    done
    
    if [ ${#missing_vars[@]} -gt 0 ]; then
        log_error "以下环境变量未设置: ${missing_vars[*]}"
        return 1
    fi
    
    log_success "环境变量检查通过"
    return 0
}

# ==================== 文件和目录工具 ====================
ensure_directory() {
    local dir_path="$1"
    if [ ! -d "$dir_path" ]; then
        log_info "创建目录: ${dir_path}"
        mkdir -p "$dir_path" || {
            log_error "创建目录失败: ${dir_path}"
            return 1
        }
    fi
    return 0
}

# 显示目录结构（包括子目录内容）
show_directory_tree() {
    local dir_path="$1"
    local max_depth="${2:-2}"  # 默认显示2层
    
    if [ ! -d "$dir_path" ]; then
        log_error "目录不存在: ${dir_path}"
        return 1
    fi
    
    echo "📁 目录结构: ${dir_path}"
    echo "----------------------------------------"
    
    # 检查是否有 tree 命令
    if command -v tree &> /dev/null; then
        tree -L ${max_depth} -h --dirsfirst "${dir_path}"
    else
        # 使用 find 命令替代
        find "${dir_path}" -maxdepth ${max_depth} -type d | while read -r dir; do
            local depth=$(echo "$dir" | sed "s|${dir_path}||" | tr -cd '/' | wc -c)
            local indent=$(printf '%*s' $((depth * 2)) '')
            local dirname=$(basename "$dir")
            [ "$dir" = "${dir_path}" ] && dirname="."
            echo "${indent}📂 ${dirname}/"
            
            # 列出该目录下的文件
            find "$dir" -maxdepth 1 -type f | while read -r file; do
                local filename=$(basename "$file")
                local size=$(ls -lh "$file" | awk '{print $5}')
                echo "${indent}  📄 ${filename} (${size})"
            done
        done
    fi
    
    echo "----------------------------------------"
    return 0
}
