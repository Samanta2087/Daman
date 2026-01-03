# Daman WinGo Bot

An AI-powered Telegram bot for WinGo game predictions using machine learning.

## Features

- 🤖 AI-powered predictions using Gradient Boosting
- 📊 Real-time game data fetching
- 📢 Multi-channel support
- ⏰ Scheduled posting with time controls
- 🎯 Accuracy tracking and learning

## Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/Samanta2087/Daman.git
   cd Daman
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   - Copy `.env.example` to `.env`
   - Fill in your credentials:
     - Get `API_ID` and `API_HASH` from https://my.telegram.org
     - Get `BOT_TOKEN` from @BotFather on Telegram
     - Set your `ADMIN_ID` (your Telegram user ID)
     - Configure your channel usernames

4. **Run the bot**
   ```bash
   python dmjson.py
   ```

## Configuration

Edit `.env` file with your credentials:

```env
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
ADMIN_ID=your_user_id
MAIN_CHANNEL=@your_channel
VIP_CHANNEL=@your_vip_channel
TEST_CHANNEL=@your_test_channel
```

## Bot Commands

- `/start` - Start the bot and see menu
- `/status` - Check bot status
- Use inline buttons for controls:
  - 🟢 Manual ON/OFF
  - ⏰ Auto Time Mode
  - 📊 Statistics

## Requirements

- Python 3.8+
- Telegram API credentials
- Bot token from @BotFather

## Security

⚠️ **Important**: Never commit your `.env` file to GitHub! It contains sensitive credentials.

## License

MIT License
