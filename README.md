# redBus Fare Tracker: Nandikotkur -> Bangalore (Telegram alerts, GitHub Actions)

## 1. Create the Telegram bot (2 min)
1. In Telegram (logged in with +918147742537) search **@BotFather** -> `/newbot` -> pick a name -> copy the **token**.
2. Open your new bot, press **Start**, send "hi".
3. Get your chat id: open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `"chat":{"id": ...}`.

## 2. Put the code on GitHub
1. Create a **private** repository, upload all files in this folder (keep the `.github/workflows` folder).
2. Repo -> Settings -> Secrets and variables -> Actions -> New repository secret:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Edit `config.yaml` (dates, filters, price limits).

## 3. Run
- Repo -> Actions -> **bus-fare-tracker** -> **Run workflow** to test. You should get a "Tracking started" message.
- After that it runs automatically every 2 hours.
- If the run says 0 buses parsed, download the **debug** artifact from the run and share it so the parser can be tuned.

## Local test (optional)
```
pip install -r requirements.txt && playwright install chromium
cp .env.example .env   # fill token
python tracker.py --get-chat-id
python tracker.py --test-notify
python tracker.py --debug
```

## Notes
- GitHub cron runs on UTC and can be delayed several minutes. Scheduled workflows are paused after 60 days of no repo activity (the fare commits usually keep it active).
- redBus has no public API and changes its pages; scraping may break or be blocked, and may conflict with its terms. Personal use only.
- Never commit your `.env` or token.
