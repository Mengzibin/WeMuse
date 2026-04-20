#!/usr/bin/env bash
# 构建 macOS .app 壳包：双击启动 / Spotlight 搜索启动。
# 内部仍然调用项目里的 run.sh（venv + src.main），所以改代码不需要重建 .app。
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
APP_NAME="微信聊天助手"
BUILD_DIR="$PROJECT_DIR/build"
APP_DIR="$BUILD_DIR/$APP_NAME.app"

echo "▶ 清理旧构建"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

echo "▶ 生成 Info.plist"
cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>com.local.wechat-assistant</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

echo "▶ 生成 launcher（内部调 run.sh）"
cat > "$APP_DIR/Contents/MacOS/launcher" <<LAUNCHER
#!/usr/bin/env bash
# 由 .app 双击 / Spotlight 启动时执行。
set -eo pipefail
PROJECT="$PROJECT_DIR"
LOG="\$HOME/Library/Logs/wechat-assistant.log"
mkdir -p "\$(dirname "\$LOG")"
cd "\$PROJECT"
{
    echo ""
    echo "===== \$(date) 启动 ====="
} >> "\$LOG"

# 首次启动时自动建 venv + 装依赖（会阻塞几十秒，日志里可见进度）
if [ ! -d ".venv" ]; then
    /usr/bin/env python3 -m venv .venv >> "\$LOG" 2>&1
    .venv/bin/pip install --upgrade pip >> "\$LOG" 2>&1
    .venv/bin/pip install -r requirements.txt >> "\$LOG" 2>&1
fi

# 进入主程序；-u 强制无缓冲，print 立刻落盘，便于 tail -f 观察
exec .venv/bin/python -u -m src.main >> "\$LOG" 2>&1
LAUNCHER
chmod +x "$APP_DIR/Contents/MacOS/launcher"

echo "✅ 构建完成： $APP_DIR"
echo ""
echo "------------------------------------------------------------"
echo "下一步 —— 让 Spotlight 能搜到它："
echo ""
echo "  mkdir -p ~/Applications"
echo "  mv \"$APP_DIR\" ~/Applications/"
echo ""
echo "然后 ⌘ + 空格 → 输入「微信聊天助手」→ 回车启动。"
echo ""
echo "首次启动 macOS 会依次弹 3 个权限请求，全部点「允许」："
echo "  1. 屏幕录制  —— 截图功能需要"
echo "  2. 辅助功能  —— 全局热键 ⌘⇧R / ⌘⇧G 需要"
echo "  3. 自动化（控制系统事件）—— 窗口激活需要"
echo ""
echo "日志在： ~/Library/Logs/wechat-assistant.log"
echo "------------------------------------------------------------"
