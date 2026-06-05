#!/bin/bash
# 通用通知脚本 - 支持飞书和Telegram

set -e

# 引入通用函数库
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

# ==================== 环境变量配置 ====================
NOTIFICATION_PROVIDER="${NOTIFICATION_PROVIDER:-none}"  # feishu | telegram | all | none
NOTIFICATION_TYPE="${NOTIFICATION_TYPE:-deployment}"    # deployment | rollback
STATUS="${STATUS:-success}"                              # success | failure
ENVIRONMENT="${ENVIRONMENT:-dev}"                        # dev | test | prod | uat
BINARY_NAME="${BINARY_NAME:-demo-core-service}"
VERSION="${VERSION:-}"
GIT_COMMIT="${GIT_COMMIT:-}"
OPERATOR="${OPERATOR:-${GITHUB_ACTOR:-unknown}}"
OPERATOR_AVATAR="${OPERATOR_AVATAR:-}"                   # 操作人员头像URL
ERROR_MESSAGE="${ERROR_MESSAGE:-}"
WORKFLOW_URL="${WORKFLOW_URL:-}"

# 部署配置
DEPLOYMENT_TARGET="${DEPLOYMENT_TARGET:-}"
NAMESPACE="${NAMESPACE:-}"
CONFIG_YAML="${CONFIG_YAML:-}"
CONFIGMAP_NAME="${CONFIGMAP_NAME:-}"
HTTP_PORT="${HTTP_PORT:-}"
RPC_PORT="${RPC_PORT:-}"
DEPLOYMENT_REPLICAS="${DEPLOYMENT_REPLICAS:-}"
CPU_REQUEST="${CPU_REQUEST:-}"
CPU_LIMIT="${CPU_LIMIT:-}"
MEMORY_REQUEST="${MEMORY_REQUEST:-}"
MEMORY_LIMIT="${MEMORY_LIMIT:-}"

# 飞书配置
FEISHU_WEBHOOK_URL="${FEISHU_WEBHOOK_URL:-}"

# Telegram 配置
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

# ==================== 函数定义 ====================

# 获取仓库简称
get_repo_name() {
    if [ -n "${GITHUB_REPOSITORY:-}" ]; then
        echo "${GITHUB_REPOSITORY##*/}"
    else
        echo "${BINARY_NAME}"
    fi
}

# 获取环境标签
get_env_label() {
    case "${ENVIRONMENT}" in
        prod) echo "🔴 生产环境" ;;
        test) echo "🟡 测试环境" ;;
        dev)  echo "🟢 开发环境" ;;
        uat)  echo "🔵 UAT环境" ;;
        *)    echo "⚪ ${ENVIRONMENT}" ;;
    esac
}

# 获取环境emoji
get_env_emoji() {
    case "${ENVIRONMENT}" in
        prod) echo "🔴" ;;
        test) echo "🟡" ;;
        dev)  echo "🟢" ;;
        uat)  echo "🔵" ;;
        *)    echo "⚪" ;;
    esac
}

# 获取操作人员头像URL
get_operator_avatar() {
    if [ -n "${OPERATOR_AVATAR}" ]; then
        echo "${OPERATOR_AVATAR}"
    elif [ -n "${OPERATOR}" ] && [ "${OPERATOR}" != "unknown" ]; then
        # GitHub 用户头像 URL
        echo "https://github.com/${OPERATOR}.png"
    else
        # 默认头像
        echo "https://avatars.githubusercontent.com/u/0"
    fi
}

# 获取状态信息
get_status_info() {
    if [ "${STATUS}" = "success" ]; then
        if [ "${NOTIFICATION_TYPE}" = "deployment" ]; then
            echo "title=✅ 部署成功"
            echo "color=green"
            echo "icon=✅"
            echo "text=部署成功"
        else
            echo "title=✅ 回滚成功"
            echo "color=green"
            echo "icon=✅"
            echo "text=回滚成功"
        fi
    else
        if [ "${NOTIFICATION_TYPE}" = "deployment" ]; then
            echo "title=❌ 部署失败"
            echo "color=red"
            echo "icon=❌"
            echo "text=部署失败"
        else
            echo "title=❌ 回滚失败"
            echo "color=red"
            echo "icon=❌"
            echo "text=回滚失败"
        fi
    fi
}

# 发送飞书通知
send_feishu_notification() {
    if [ -z "${FEISHU_WEBHOOK_URL}" ]; then
        log_error "飞书通知：FEISHU_WEBHOOK_URL 未配置，跳过"
        return 1
    fi

    log_info "发送飞书通知..."
    
    local status_info=$(get_status_info)
    local title=$(echo "${status_info}" | grep "^title=" | cut -d= -f2-)
    local color=$(echo "${status_info}" | grep "^color=" | cut -d= -f2-)
    local env_label=$(get_env_label)
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local operation_type=$([ "${NOTIFICATION_TYPE}" = "deployment" ] && echo "📦 部署" || echo "⏮️ 回滚")
    local version_display="${VERSION:-${GIT_COMMIT:-N/A}}"
    local operator_avatar=$(get_operator_avatar)

    # 构建飞书消息
    if [ "${STATUS}" = "success" ]; then
        # 构建部署配置信息
        local deployment_info=""
        if [ -n "${DEPLOYMENT_TARGET}" ]; then
            deployment_info="${deployment_info}**部署目标:** ${DEPLOYMENT_TARGET}\n"
        fi
        if [ -n "${NAMESPACE}" ]; then
            deployment_info="${deployment_info}**命名空间:** ${NAMESPACE}\n"
        fi
        if [ -n "${DEPLOYMENT_REPLICAS}" ]; then
            deployment_info="${deployment_info}**副本数:** ${DEPLOYMENT_REPLICAS}\n"
        fi
        if [ -n "${HTTP_PORT}" ] || [ -n "${RPC_PORT}" ]; then
            local ports=""
            [ -n "${HTTP_PORT}" ] && ports="${ports}HTTP:${HTTP_PORT} "
            [ -n "${RPC_PORT}" ] && ports="${ports}RPC:${RPC_PORT} "
            deployment_info="${deployment_info}**端口配置:** ${ports}\n"
        fi
        if [ -n "${CPU_REQUEST}" ] && [ -n "${MEMORY_REQUEST}" ]; then
            deployment_info="${deployment_info}**资源请求:** CPU:${CPU_REQUEST} / Memory:${MEMORY_REQUEST}\n"
        fi
        if [ -n "${CPU_LIMIT}" ] && [ -n "${MEMORY_LIMIT}" ]; then
            deployment_info="${deployment_info}**资源限制:** CPU:${CPU_LIMIT} / Memory:${MEMORY_LIMIT}\n"
        fi
        if [ -n "${CONFIGMAP_NAME}" ]; then
            deployment_info="${deployment_info}**ConfigMap:** ${CONFIGMAP_NAME}\n"
        fi
        if [ -n "${CONFIG_YAML}" ]; then
            deployment_info="${deployment_info}**配置文件:** ${CONFIG_YAML}"
        fi
        
        cat > /tmp/feishu_payload.json <<EOF
{
  "msg_type": "interactive",
  "card": {
    "header": {
      "title": {
        "tag": "plain_text",
        "content": "${title}"
      },
      "template": "${color}"
    },
    "elements": [
      {
        "tag": "div",
        "fields": [
          {
            "is_short": true,
            "text": {
              "tag": "lark_md",
              "content": "**服务名称:**\n${BINARY_NAME}"
            }
          },
          {
            "is_short": true,
            "text": {
              "tag": "lark_md",
              "content": "**部署环境:**\n${env_label}"
            }
          },
          {
            "is_short": true,
            "text": {
              "tag": "lark_md",
              "content": "**操作类型:**\n${operation_type}"
            }
          },
          {
            "is_short": true,
            "text": {
              "tag": "lark_md",
              "content": "**操作人员:**\n[${OPERATOR}](https://github.com/${OPERATOR})"
            }
          }
        ]
      },
      {
        "tag": "div",
        "fields": [
          {
            "is_short": false,
            "text": {
              "tag": "lark_md",
              "content": "**版本信息:**\n${version_display}"
            }
          },
          {
            "is_short": false,
            "text": {
              "tag": "lark_md",
              "content": "**完成时间:**\n${timestamp}"
            }
          }
        ]
      },
      {
        "tag": "div",
        "text": {
          "tag": "lark_md",
          "content": "${deployment_info}"
        }
      },
      {
        "tag": "hr"
      },
      {
        "tag": "div",
        "text": {
          "tag": "lark_md",
          "content": "**Workflow:** [查看详情](${WORKFLOW_URL})"
        }
      }
    ]
  }
}
EOF
    else
        # 失败通知
        local error_msg="${ERROR_MESSAGE:-操作失败，请查看日志获取详细信息}"
        cat > /tmp/feishu_payload.json <<EOF
{
  "msg_type": "interactive",
  "card": {
    "header": {
      "title": {
        "tag": "plain_text",
        "content": "${title}"
      },
      "template": "${color}"
    },
    "elements": [
      {
        "tag": "div",
        "fields": [
          {
            "is_short": true,
            "text": {
              "tag": "lark_md",
              "content": "**服务名称:**\n${BINARY_NAME}"
            }
          },
          {
            "is_short": true,
            "text": {
              "tag": "lark_md",
              "content": "**部署环境:**\n${env_label}"
            }
          },
          {
            "is_short": true,
            "text": {
              "tag": "lark_md",
              "content": "**操作类型:**\n${operation_type}"
            }
          },
          {
            "is_short": true,
            "text": {
              "tag": "lark_md",
              "content": "**操作人员:**\n[${OPERATOR}](https://github.com/${OPERATOR})"
            }
          }
        ]
      },
      {
        "tag": "div",
        "fields": [
          {
            "is_short": false,
            "text": {
              "tag": "lark_md",
              "content": "**版本信息:**\n${version_display}"
            }
          },
          {
            "is_short": false,
            "text": {
              "tag": "lark_md",
              "content": "**失败时间:**\n${timestamp}"
            }
          }
        ]
      },
      {
        "tag": "div",
        "text": {
          "tag": "lark_md",
          "content": "**错误信息:**\n${error_msg}"
        }
      },
      {
        "tag": "hr"
      },
      {
        "tag": "div",
        "text": {
          "tag": "lark_md",
          "content": "**Workflow:** [查看详情](${WORKFLOW_URL})"
        }
      }
    ]
  }
}
EOF
    fi

    # 发送请求
    local response=$(curl -s -X POST "${FEISHU_WEBHOOK_URL}" \
        -H "Content-Type: application/json" \
        -d @/tmp/feishu_payload.json)
    
    rm -f /tmp/feishu_payload.json
    
    if echo "${response}" | grep -q '"code":0'; then
        log_info "飞书通知发送成功"
        return 0
    else
        log_error "飞书通知发送失败: ${response}"
        return 1
    fi
}

# 发送Telegram通知
send_telegram_notification() {
    if [ -z "${TELEGRAM_BOT_TOKEN}" ] || [ -z "${TELEGRAM_CHAT_ID}" ]; then
        log_error "Telegram通知：TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未配置，跳过"
        return 1
    fi

    log_info "发送Telegram通知..."
    
    local status_info=$(get_status_info)
    local icon=$(echo "${status_info}" | grep "^icon=" | cut -d= -f2-)
    local status_text=$(echo "${status_info}" | grep "^text=" | cut -d= -f2-)
    local env_emoji=$(get_env_emoji)
    local repo_name=$(get_repo_name)
    local operation_type=$([ "${NOTIFICATION_TYPE}" = "deployment" ] && echo "📦 部署" || echo "⏮️ 回滚")
    local version_display="${VERSION:-${GIT_COMMIT:-latest}}"
    local operator_avatar=$(get_operator_avatar)

    # 构建消息
    if [ "${STATUS}" = "success" ]; then
        # 构建部署配置信息
        local deployment_info=""
        [ -n "${DEPLOYMENT_TARGET}" ] && deployment_info="${deployment_info}*目标:* \`${DEPLOYMENT_TARGET}\`\n"
        [ -n "${NAMESPACE}" ] && deployment_info="${deployment_info}*命名空间:* \`${NAMESPACE}\`\n"
        [ -n "${DEPLOYMENT_REPLICAS}" ] && deployment_info="${deployment_info}*副本数:* \`${DEPLOYMENT_REPLICAS}\`\n"
        if [ -n "${HTTP_PORT}" ] || [ -n "${RPC_PORT}" ]; then
            local ports=""
            [ -n "${HTTP_PORT}" ] && ports="${ports}HTTP:${HTTP_PORT} "
            [ -n "${RPC_PORT}" ] && ports="${ports}RPC:${RPC_PORT} "
            deployment_info="${deployment_info}*端口:* \`${ports}\`\n"
        fi
        [ -n "${CPU_REQUEST}" ] && [ -n "${MEMORY_REQUEST}" ] && deployment_info="${deployment_info}*资源:* \`CPU:${CPU_REQUEST}/Memory:${MEMORY_REQUEST}\`\n"
        [ -n "${CONFIGMAP_NAME}" ] && deployment_info="${deployment_info}*ConfigMap:* \`${CONFIGMAP_NAME}\`\n"
        
        MESSAGE="*${icon} [${repo_name}]*
*服务:* \`${BINARY_NAME}\`
*环境:* ${env_emoji} \`${ENVIRONMENT}\`
*操作:* ${operation_type}
*版本:* \`${version_display}\`
*操作人:* [${OPERATOR}](https://github.com/${OPERATOR})
*状态:* ${status_text}
${deployment_info}*Workflow:* [查看详情](${WORKFLOW_URL})

[​](${operator_avatar})"
    else
        local error_msg="${ERROR_MESSAGE:-操作失败，请查看日志获取详细信息}"
        MESSAGE="*${icon} [${repo_name}]*
*服务:* \`${BINARY_NAME}\`
*环境:* ${env_emoji} \`${ENVIRONMENT}\`
*操作:* ${operation_type}
*版本:* \`${version_display}\`
*操作人:* [${OPERATOR}](https://github.com/${OPERATOR})
*状态:* ${status_text}
*错误:* ${error_msg}
*Workflow:* [查看详情](${WORKFLOW_URL})

[​](${operator_avatar})"
    fi

    # 发送请求
    local response=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d parse_mode=Markdown \
        -d disable_web_page_preview=true \
        -d text="${MESSAGE}")
    
    if echo "${response}" | grep -q '"ok":true'; then
        log_info "Telegram通知发送成功"
        return 0
    else
        log_error "Telegram通知发送失败: ${response}"
        return 1
    fi
}

# ==================== 主逻辑 ====================

main() {
    log_info "================================================"
    log_info "  通知系统"
    log_info "  提供商: ${NOTIFICATION_PROVIDER}"
    log_info "  类型: ${NOTIFICATION_TYPE}"
    log_info "  状态: ${STATUS}"
    log_info "  环境: ${ENVIRONMENT}"
    log_info "================================================"
    echo ""

    # 验证必需参数
    if [ -z "${WORKFLOW_URL}" ]; then
        log_error "WORKFLOW_URL 未设置"
        exit 1
    fi

    case "${NOTIFICATION_PROVIDER}" in
        feishu)
            log_info "发送飞书通知..."
            send_feishu_notification || exit 1
            ;;
        telegram)
            log_info "发送Telegram通知..."
            send_telegram_notification || exit 1
            ;;
        all)
            log_info "发送所有通知..."
            local feishu_result=0
            local telegram_result=0
            
            send_feishu_notification || feishu_result=$?
            send_telegram_notification || telegram_result=$?
            
            if [ ${feishu_result} -ne 0 ] && [ ${telegram_result} -ne 0 ]; then
                log_error "所有通知渠道都发送失败"
                exit 1
            elif [ ${feishu_result} -ne 0 ]; then
                log_info "Telegram通知成功，但飞书通知失败（已忽略）"
            elif [ ${telegram_result} -ne 0 ]; then
                log_info "飞书通知成功，但Telegram通知失败（已忽略）"
            else
                log_info "所有通知发送成功"
            fi
            ;;
        none)
            log_info "通知已禁用，跳过"
            ;;
        *)
            log_error "未知的通知提供商: ${NOTIFICATION_PROVIDER}"
            log_error "支持的值: feishu | telegram | all | none"
            exit 1
            ;;
    esac

    echo ""
    log_info "================================================"
    log_info "  通知发送完成"
    log_info "================================================"
}

# 执行主函数
main
