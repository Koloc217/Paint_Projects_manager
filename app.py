"""Launcher: `python app.py [--port N] [--host ADDR]` starts the web app, the Telegram bot and the scheduler.

The code lives in the hub/ package (hub/web, hub/bot, hub/jobs, hub/core); this file only keeps the familiar command.
"""
from hub.web.server import main

if __name__ == "__main__":
    main()
