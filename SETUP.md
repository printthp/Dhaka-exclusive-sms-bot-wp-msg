# Dhaka Exclusive Bot — Hostinger VPS Setup Guide

## 1. VPS Preparation (Hostinger Ubuntu 22.04/24.04)

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3 python3-pip python3-venv nginx git curl

# Create app user (optional but recommended)
sudo useradd -m -s /bin/bash dhakabot
sudo usermod -aG sudo dhakabot
```

## 2. Clone & Setup App

```bash
# As your app user
cd /home/YOUR_VPS_USER
git clone https://github.com/printthp/Dhaka-exclusive-sms-bot-wp-msg.git
cd Dhaka-exclusive-sms-bot-wp-msg

# Create venv & install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env file
cp .env.example .env
nano .env   # ← Fill in ALL values (API keys, Telegram token, etc.)
```

## 3. systemd Service

```bash
# Edit dhaka-bot.service → replace YOUR_VPS_USER with actual username
sudo cp dhaka-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable dhaka-bot
sudo systemctl start dhaka-bot
sudo systemctl status dhaka-bot   # Verify running
```

## 4. Nginx Reverse Proxy

```bash
# Create proxy_params
sudo tee /etc/nginx/proxy_params <<'EOF'
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_redirect off;
proxy_buffering off;
proxy_read_timeout 120s;
proxy_connect_timeout 10s;
EOF

# Copy & edit nginx config
sudo cp nginx-dhaka-bot.conf /etc/nginx/sites-available/dhaka-bot
# → Replace YOUR_DOMAIN_OR_IP with actual domain/IP
sudo nano /etc/nginx/sites-available/dhaka-bot

# Enable site
sudo ln -s /etc/nginx/sites-available/dhaka-bot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default   # Remove default
sudo nginx -t                                  # Test config
sudo systemctl reload nginx
```

## 5. SSL with Let's Encrypt (recommended)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR_DOMAIN
# Then uncomment the HTTPS server block in nginx config & reload
```

## 6. Auto-Deploy Setup

### Option A: GitHub Actions (SSH)
1. On VPS: `ssh-keygen -t ed25519 -f ~/.ssh/github-actions -N ""`
2. Add public key to `~/.ssh/authorized_keys`: `cat ~/.ssh/github-actions.pub >> ~/.ssh/authorized_keys`
3. In GitHub repo → Settings → Secrets and variables → Actions:
   - `VPS_HOST`: your VPS IP
   - `VPS_USER`: your VPS username
   - `VPS_SSH_KEY`: `cat ~/.ssh/github-actions` (the private key)
   - `TELEGRAM_BOT_TOKEN`: (optional, for deploy notifications)

### Option B: Webhook (no GitHub Actions needed)
1. The `/deploy-webhook` route is already in app.py
2. Set `DEPLOY_WEBHOOK_SECRET` in `.env`
3. In GitHub repo → Settings → Webhooks → Add webhook:
   - Payload URL: `https://YOUR_DOMAIN/deploy-webhook`
   - Content type: `application/json`
   - Secret: same as `DEPLOY_WEBHOOK_SECRET`
   - Events: "Just the push event"

## 7. Telegram Bot Setup
1. Create bot via @BotFather → get token
2. Set token in `.env` as `TELEGRAM_BOT_TOKEN`
3. Set your Telegram user ID in `.env` as `ADMIN_TELEGRAM_IDS` (comma-separated if multiple)
4. Set webhook: The app auto-registers on startup, or run:
```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://YOUR_DOMAIN/tg-webhook"
```

## 8. Verify Everything

```bash
# Check service
sudo systemctl status dhaka-bot

# Check endpoints
curl http://localhost:5000/api/telegram-status
curl http://localhost:5000/api/improvement-stats
curl http://localhost:5000/

# Check nginx
curl -I http://YOUR_DOMAIN/

# Tail logs
sudo journalctl -u dhaka-bot -f
```

## 9. Common Issues

| Problem | Fix |
|---------|-----|
| 502 Bad Gateway | `sudo systemctl restart dhaka-bot` |
| Port 5000 not reachable | Check firewall: `sudo ufw allow 5000` or use nginx |
| Telegram not responding | Verify webhook: check `/api/telegram-status` |
| Deploy not triggering | Check GitHub Actions logs or webhook deliveries |
| SQLite locked | Only one gunicorn worker? Set `--workers 1` in service file |
