#!/usr/bin/env python3
"""Local-only helper: print private chat IDs without putting Bot Token in a URL/history."""
import getpass
import json
import urllib.error
import urllib.request
from fetch import CTX

def main():
    token=getpass.getpass('BotFather Token (hidden, never saved): ').strip()
    if not token:
        raise SystemExit('Token not entered')
    request=urllib.request.Request(f'https://api.telegram.org/bot{token}/getUpdates')
    try:
        with urllib.request.urlopen(request,timeout=30,context=CTX) as r: data=json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f'Telegram HTTP {e.code}; check Bot Token') from None
    except Exception:
        raise SystemExit('Telegram connection failed; no Token was saved') from None
    if not data.get('ok'):
        raise SystemExit('Telegram did not return updates')
    chats={str(x['message']['chat']['id']) for x in data.get('result',[]) if x.get('message',{}).get('chat',{}).get('type')=='private'}
    if not chats:
        print('Send /start and a short message to YOUR new bot, then rerun. If a webhook is configured, resolve it before using getUpdates; this helper does not remove webhooks.')
    else:
        print('Private chat IDs (choose your own):')
        for chat in sorted(chats):print(chat)
if __name__=='__main__':main()
