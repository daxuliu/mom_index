#!/bin/bash
# 宝妈指数 - 腾讯云轻量服务器一键部署脚本
# 使用方法: 在本地执行 bash deploy_cloud.sh root@你的服务器IP

set -e

REMOTE_HOST=${1:?"请输入服务器IP, 用法: bash deploy_cloud.sh root@你的服务器IP"}
REMOTE_USER=$(echo $REMOTE_HOST | cut -d@ -f1)
REMOTE_IP=$(echo $REMOTE_HOST | cut -d@ -f2)

echo "============================================"
echo " 宝妈指数部署脚本"
echo " 目标: $REMOTE_USER@$REMOTE_IP"
echo "============================================"

# 1. 本地打包
echo ""
echo "📦 打包项目..."
cd "$(dirname "$0")"
tar czf mom-index.tar.gz \
    --exclude='.git' \
    --exclude='.vercel' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    collectors/ analyzer/ frontend/ public/ api_server.py pipeline.py requirements.txt vercel.json deploy_cloud.sh
echo "✅ 打包完成"

# 2. 上传到服务器
echo ""
echo "⬆️ 上传到服务器..."
scp mom-index.tar.gz $REMOTE_USER@$REMOTE_IP:/tmp/
echo "✅ 上传完成"

# 3. 在服务器上执行部署
echo ""
echo "🔧 服务器端配置..."
ssh $REMOTE_USER@$REMOTE_IP '
set -e

# 安装依赖
apt update -qq
apt install -y python3 python3-pip nginx -qq

# 创建目录
rm -rf /app
mkdir -p /app
cd /app

# 解压
tar xzf /tmp/mom-index.tar.gz

# 安装 Python 依赖
pip3 install -r requirements.txt --break-system-packages

# 创建 systemd 服务
cat > /etc/systemd/system/mom-index.service << EOF
[Unit]
Description=Mom Index API Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/app
ExecStart=/usr/bin/python3 /app/api_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 启动服务
systemctl daemon-reload
systemctl enable mom-index
systemctl restart mom-index

# 等待服务启动
sleep 2

# 检查状态
if curl -s http://localhost:8766/api/health | grep -q "ok"; then
    echo "✅ API 服务启动成功"
else
    echo "❌ API 服务启动失败, 检查日志: journalctl -u mom-index"
    exit 1
fi

# 配置 Nginx
cat > /etc/nginx/sites-available/mom-index << EOF
server {
    listen 80 default_server;
    server_name _;
    
    client_max_body_size 50m;
    
    location / {
        proxy_pass http://127.0.0.1:8766;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

# 启用站点
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/mom-index /etc/nginx/sites-enabled/

# 测试 Nginx 配置
nginx -t

# 重启 Nginx
systemctl restart nginx

# 设置定时任务 (每天 9:00 自动跑 pipeline)
(crontab -l 2>/dev/null | grep -v "mom-index-pipeline"; echo "0 9 * * * cd /app && /usr/bin/python3 /app/pipeline.py >> /app/pipeline.log 2>&1") | crontab -

echo ""
echo "============================================"
echo " 🎉 部署完成!"
echo ""
echo " 访问地址:"
echo "   本机测试: http://localhost:8766"
echo "   公网访问: http://'"$REMOTE_IP"'"
echo ""
echo " 常用命令:"
echo "   查看状态: systemctl status mom-index"
echo "   查看日志: journalctl -u mom-index -f"
echo "   重启服务: systemctl restart mom-index"
echo "   手动跑pipeline: cd /app && python3 pipeline.py"
echo "============================================"
'

# 4. 清理
echo ""
echo "🧹 清理临时文件..."
rm -f mom-index.tar.gz

echo ""
echo "✅ 完成! 请访问 http://$REMOTE_IP"
