#!/usr/bin/env python
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = input("Enter your API ID: ")
API_HASH = input("Enter your API HASH: ")

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    # Save the session string to use later
    print(client.session.save())