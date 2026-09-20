#!/bin/bash
set -e

# 确保 chrome 用户目录存在且权限正确
mkdir -p /home/chrome/data
chown -R chrome:chrome /home/chrome

# 清除可能残留的锁文件（容器重启时）
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
rm -f /home/chrome/data/SingletonLock /home/chrome/data/SingletonCookie /home/chrome/data/SingletonSocket

# 先启动 Xvfb
Xvfb :99 -screen 0 1280x800x24 -ac &
sleep 1

# 禁用屏保和 DPMS（防止无操作黑屏）
export DISPLAY=:99
xset s off
xset s noblank
xset -dpms
echo "[entrypoint] 屏保和 DPMS 已禁用"

# 启动 supervisord（管理 x11vnc + noVNC + Chrome + cdp-proxy）
/usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf &
SPID=$!

# 等待 Chrome CDP 就绪，然后注入反检测脚本
echo "[entrypoint] 等待 Chrome CDP 就绪..."
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
        echo "[entrypoint] Chrome CDP 就绪，注入反检测脚本"
        # 通过 CDP 向所有新页面注入反检测 JS
        SCRIPT=$(cat /opt/anti-detect.js | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
        curl -s -X POST "http://127.0.0.1:9222/json/new?about:blank" > /dev/null 2>&1 || true
        # 获取第一个 page 的 ws url 并注入
        PAGE_WS=$(curl -s http://127.0.0.1:9222/json | python3 -c "
import json,sys
pages=[p for p in json.load(sys.stdin) if p.get('type')=='page']
if pages: print(pages[0]['webSocketDebuggerUrl'])
" 2>/dev/null)
        if [ -n "$PAGE_WS" ]; then
            # 用 CDP 注入 Page.addScriptToEvaluateOnNewDocument
            python3 -c "
import asyncio, json, websockets
async def inject():
    async with websockets.connect('$PAGE_WS') as ws:
        cmd = json.dumps({
            'id': 1,
            'method': 'Page.addScriptToEvaluateOnNewDocument',
            'params': {'source': open('/opt/anti-detect.js').read()}
        })
        await ws.send(cmd)
        resp = await ws.recv()
        print('[entrypoint] 反检测脚本注入成功')
asyncio.run(inject())
" 2>/dev/null || echo "[entrypoint] 注入失败（非关键）"
        fi
        break
    fi
    sleep 1
done

wait $SPID
