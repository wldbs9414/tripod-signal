#!/usr/bin/env python3
"""Tripod-only settled-close notifications; does not modify upstream rule code."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import html
import json
import math
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SIGNAL = ROOT / 'data/signal.json'
STATE = ROOT / 'data/telegram_state.json'
HISTORY = ROOT / 'data/telegram_history.jsonl'
SOURCE = 'https://github.com/imtaehyun/tripod-signal'


def expected_weekday(now: dt.datetime) -> str:
    """Most recently closed US weekday; holidays intentionally fail closed.

    This is not a holiday calendar. On an exchange holiday, old data yields a
    factual no-new-data message, never a fresh trade from the previous session.
    """
    ny = now.astimezone(ZoneInfo('America/New_York'))
    day = ny.date()
    if ny.hour < 16:
        day -= dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day.isoformat()


def digest(strategy: dict, auxiliary: dict | None = None) -> str:
    # Exclude generated_at: rerunning the same session is not a new signal.
    value = {k: strategy['latest'][k] for k in ('date', 'gear', 'prev_gear', 'action_required', 'legs')}
    if auxiliary is not None:
        value['auxiliary'] = auxiliary
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate(strategy: dict, now: dt.datetime) -> str | None:
    latest = strategy['latest']
    day = dt.date.fromisoformat(latest['date']).isoformat()
    if latest.get('provisional'):
        raise ValueError('Refusing provisional/intraday signal')
    if latest['gear'] not in strategy['gears'] or latest['prev_gear'] not in strategy['gears']:
        raise ValueError('Unknown gear')
    for group, key in [('ma','value'), ('vix','value'), ('dd','value')]:
        value = latest['legs'][group][key]
        if not isinstance(value, (float,int)) or not math.isfinite(value):
            raise ValueError('Invalid indicator')
    expected = expected_weekday(now)
    if day != expected:
        return f'최근 마감 미국 평일 {expected} / 확보된 공통 종가 {day}'
    return None


def build_message(strategy: dict, auxiliary: dict | None = None) -> str:
    from auxiliary import calculate, describe
    if auxiliary is None:
        try:
            auxiliary = calculate(strategy)
        except (OSError, ValueError, KeyError):
            auxiliary = {'version':'aux-v1','available':False,'date':strategy['latest']['date'],'reason':'보조 지표 원자료 확인 불가'}
    latest, gears = strategy['latest'], strategy['gears']
    current, previous = gears[latest['gear']], gears[latest['prev_gear']]
    # Reuse original gear rendering and order definitions; avoid duplicating rules.
    from notify import tripod_msg
    _, body = tripod_msg(strategy)
    if latest.get('regime_only_change'):
        body += '\n시장 상태만 변경 · 목표 자산 비중은 동일해요.'
    if not latest['action_required']:
        legs = latest['legs']
        body += (f"\nNDX vs SMA250 {legs['ma']['dist_pct']:+.2f}%"
                 f" / VIX10 {legs['vix']['value']:.2f} / 낙폭 {legs['dd']['value']:.2f}%")
    return (f"<b>트라이팟 종가 신호 · {html.escape(latest['date'])}</b>\n"
            f"미국 거래일 종가 확정 기준\n\n{body}\n\n"
            '목표 비중을 전일 모델 목표와 비교한 신호예요. 실제 체결 내역이 아니에요.\n'
            '변경이 있으면 다음 거래일 실행을 검토하세요. 자동 주문은 하지 않아요.\n'
            f'\n{describe(auxiliary, strategy)}\n\n'
            f'<a href="{SOURCE}">기준 GitHub 저장소</a>')


def safe_send(text: str) -> None:
    token = os.environ.get('TELEGRAM_BOT_TOKEN','').strip()
    chat = os.environ.get('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat:
        raise RuntimeError('GitHub Secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are required')
    from fetch import CTX
    body = urllib.parse.urlencode({'chat_id':chat,'text':text,'parse_mode':'HTML','disable_web_page_preview':'true'}).encode()
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage',data=body)
    # Never log an exception containing a URL: Telegram puts the token in it.
    try:
        with urllib.request.urlopen(request,timeout=30,context=CTX) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Telegram rejected request (HTTP {exc.code}); check Secrets and Bot /start') from None
    except Exception:
        raise RuntimeError('Telegram delivery not confirmed; check network and Bot settings') from None
    if not result.get('ok'):
        raise RuntimeError('Telegram did not acknowledge delivery')


def save_state(state: dict, key: str, record: dict) -> None:
    state[key] = record
    # Bound bookkeeping; the separate append-only history retains each send.
    state = dict(list(state.items())[-400:])
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    temp.replace(STATE)
    with HISTORY.open('a') as out:
        out.write(json.dumps(record,ensure_ascii=False)+'\n')


def run(*,dry_run=False,test=False,fail=False,now=None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if fail:
        key = 'error:'+expected_weekday(now)
        if key in state and not dry_run:
            return 'duplicate error skipped'
        url = html.escape(os.environ.get('RUN_URL',''),quote=True)
        text = ('<b>트라이팟 신호 생성 실패</b>\n최신 신호를 확인하지 못했어요. '
                '이 메시지는 매수·매도 또는 유지 신호가 아니에요.')
        if url:
            text += f'\n<a href="{url}">GitHub Actions 실행 로그</a>'
        kind, day, fingerprint = 'error',expected_weekday(now),None
    elif test:
        key = 'test:'+now.isoformat()
        text = '<b>Telegram 연결 테스트</b>\n트라이팟 알림 수신 설정을 확인하는 메시지예요. 매매 신호가 아니에요.'
        kind,day,fingerprint = 'test',expected_weekday(now),None
    else:
        payload = json.loads(SIGNAL.read_text())
        if payload.get('provisional'):
            raise ValueError('Refusing a provisional payload')
        strategy = payload['strategies']['tripod']
        stale = validate(strategy,now)
        day = strategy['latest']['date']
        if stale:
            key = 'waiting:'+expected_weekday(now)
            kind,fingerprint = 'waiting',None
            text = ('<b>트라이팟 · 새 종가 데이터 미확보</b>\n'+html.escape(stale)+
                    '\n휴장 또는 데이터 공개 지연 여부를 확인해야 해요. 이전 신호를 새 매매 신호로 보내지 않아요.')
        else:
            from auxiliary import calculate
            try:
                auxiliary = calculate(strategy)
            except (OSError, ValueError, KeyError):
                auxiliary = {'version':'aux-v1','available':False,'date':day,'reason':'보조 지표 원자료 확인 불가'}
            key = 'signal:'+day
            kind,fingerprint = 'signal',digest(strategy,auxiliary)
            if key in state:
                if state[key].get('fingerprint') == fingerprint:
                    return 'same session already sent'
                key = 'revision:'+day+':'+fingerprint
                text = '<b>동일 거래일 재확인 · 지표/표시 갱신</b>\n신호·보조 지표 또는 표시 내용이 이전 발송과 달라요. 중복 주문하지 마세요.\n\n'+build_message(strategy,auxiliary)
                kind='revision'
            else:
                text = build_message(strategy,auxiliary)
        if key in state and not dry_run and kind != 'revision':
            return 'already reported'
    if dry_run:
        return text
    safe_send(text)
    record={'key':key,'kind':kind,'date':day,'sent_at':now.isoformat(),'fingerprint':fingerprint}
    if kind in ('signal','revision'):
        record['auxiliary'] = auxiliary
        record.update(target=strategy['latest']['gear'],previous=strategy['latest']['prev_gear'],action_required=strategy['latest']['action_required'])
    if kind == 'revision':
        state['signal:'+day] = record
    save_state(state,key,record)
    return f'Telegram acknowledged: {kind} ({day})'


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--test',action='store_true')
    parser.add_argument('--fail',action='store_true')
    args=parser.parse_args()
    try:
        print(run(dry_run=args.dry_run,test=args.test,fail=args.fail))
    except (Exception,SystemExit) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        sys.exit(1)
