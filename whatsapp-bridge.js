const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

const FLASK_URL = process.env.FLASK_URL || 'http://localhost:5000';
const GROUP_NAME = process.env.WHATSAPP_GROUP_NAME || '';

const client = new Client({
    authStrategy: new LocalAuth({ dataPath: './wwebjs_auth' }),
    puppeteer: {
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
});

client.on('qr', (qr) => {
    console.log('\n=== SCAN QR CODE BELOW ===\n');
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    console.log('✅ WhatsApp Bridge Ready! Groups loaded.');
});

// Handle group messages
client.on('message_create', async (msg) => {
    try {
        const chat = await msg.getChat();
        
        // Only process group messages
        if (!chat.isGroup) return;
        
        // Filter by group name if set
        if (GROUP_NAME && !chat.name.toLowerCase().includes(GROUP_NAME.toLowerCase())) {
            return;
        }
        
        const sender = msg.author || msg.from;
        const senderName = msg._data.notifyName || 'Member';
        
        console.log(`[GROUP: ${chat.name}] ${senderName}: ${msg.body}`);
        
        // Forward to Flask backend
        const payload = {
            group_id: chat.id._serialized,
            group_name: chat.name,
            sender_id: sender,
            sender_name: senderName,
            message: msg.body,
            is_group: true,
            timestamp: Date.now()
        };
        
        const response = await axios.post(`${FLASK_URL}/group-webhook`, payload, {
            timeout: 30000
        });
        
        // If Flask wants to reply to group
        if (response.data && response.data.reply) {
            await msg.reply(response.data.reply);
            console.log(`🤖 Bot replied: ${response.data.reply.substring(0, 100)}`);
        }
        
    } catch (err) {
        console.error('Bridge error:', err.message);
    }
});

// Send message to any group (admin command)
client.on('message_create', async (msg) => {
    try {
        const chat = await msg.getChat();
        if (!chat.isGroup) return;
        
        // Admin commands
        const body = msg.body.trim().toLowerCase();
        
        if (body === '!status') {
            await msg.reply('✅ Bot Active\n🤖 Powered by Gemini AI\n📦 Products synced');
        }
        if (body === '!help') {
            await msg.reply(
                '📋 *AI Bot Commands*\n\n' +
                '!status - Bot status\n' +
                '!help - This menu\n' +
                '!products - Product list\n\n' +
                'Or just ask anything!'
            );
        }
        if (body === '!products') {
            try {
                const res = await axios.get(`${FLASK_URL}/api/products`, { timeout: 10000 });
                const products = res.data.products || [];
                let list = '📦 *Our Products*\n\n';
                products.slice(0, 10).forEach(p => {
                    list += `• ${p.name}: ${p.price}৳\n`;
                });
                await msg.reply(list);
            } catch (e) {
                await msg.reply('⚠️ Product list loading failed');
            }
        }
    } catch (err) {
        console.error('Command error:', err.message);
    }
});

// Graceful shutdown
process.on('SIGINT', async () => {
    console.log('\n🛑 Shutting down...');
    await client.destroy();
    process.exit(0);
});

client.initialize();
