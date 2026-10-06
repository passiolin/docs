#!/bin/bash
# 飞书机器人通知脚本
# 用法: ./feishu.sh <状态> [额外信息]

set -e

STATUS=$1          # success / failure / start
EXTRA_MSG=${2:-""} # 可选的额外信息

# 颜色配置
if [ "$STATUS" == "success" ]; then
    COLOR="green"
    TITLE="✅ 构建成功"
    EMOJI="🎉"
elif [ "$STATUS" == "failure" ]; then
    COLOR="red"
    TITLE="❌ 构建失败"
    EMOJI="💥"
elif [ "$STATUS" == "start" ]; then
    COLOR="blue"
    TITLE="🚀 开始构建"
    EMOJI="⏳"
else
    COLOR="grey"
    TITLE="ℹ️ 构建通知"
    EMOJI="📢"
fi

# 获取 Git 信息
COMMIT_MSG=$(git log -1 --pretty=%s 2>/dev/null || echo "未知")
COMMIT_AUTHOR=$(git log -1 --pretty=%an 2>/dev/null || echo "未知")
BRANCH_NAME=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "未知")

# 计算构建耗时（如果传入）
DURATION=${BUILD_DURATION:-"-"}

# 构造飞书消息体
JSON_PAYLOAD=$(cat <<EOF
{
    "msg_type": "interactive",
    "card": {
        "config": {"wide_screen_mode": true},
        "header": {
            "title": {"tag": "plain_text", "content": "${TITLE}"},
            "template": "${COLOR}"
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**项目名称：** ${JOB_NAME:-未知}\n**构建编号：** #${BUILD_NUMBER:-未知}\n**代码分支：** ${BRANCH_NAME}\n**提交版本：** \'${VERSION:-未知}\'\n**提交作者：** ${COMMIT_AUTHOR}\n**提交信息：** ${COMMIT_MSG}\n**构建耗时：** ${DURATION}"
                }
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看构建详情"},
                        "type": "primary",
                        "url": "${BUILD_URL}job/${JOB_NAME}/${BUILD_NUMBER}/"
                    }
                ]
            }
EOF
)

# 如果有额外信息，添加分区和额外内容
if [ -n "$EXTRA_MSG" ]; then
    JSON_PAYLOAD=$(echo "$JSON_PAYLOAD" | sed '$ d')
    JSON_PAYLOAD+=$(cat <<EOF
,
            {
                "tag": "hr"
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**附加信息：**\n\`\`\`\n${EXTRA_MSG}\n\`\`\`"
                }
            }
        ]
    }
}
EOF
)
else
    JSON_PAYLOAD+=$(cat <<EOF
        ]
    }
}
EOF
)
fi

# 发送请求
curl -s -X POST "${FEISHU_WEBHOOK}" \
    -H "Content-Type: application/json" \
    -d "$JSON_PAYLOAD" || echo "飞书通知发送失败"

echo "飞书通知已发送 [${STATUS}]"