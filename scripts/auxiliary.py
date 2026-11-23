"""Read-only context for Tripod messages. Never changes regime, gear or orders."""
from __future__ import annotations
import math
import importlib.util
from functools import lru_cache
from pathlib import Path

LONG_WINDOWS = (225, 250, 275)
MA_WINDOWS = (50, 200, *LONG_WINDOWS)
VERSION = 'aux-v1'


@lru_cache(maxsize=1)
def original_signal():
    # signal.py shares a name with Python's stdlib module; load by exact path.
    spec=importlib.util.spec_from_file_location('tripod_original_signal',Path(__file__).with_name('signal.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _position(close, values):
    if close > max(values):
        return '상단'
    if close < min(values):
        return '하단'
    return '구간 내·선 접촉'


def calculate(strategy, aligned=None):
    """Use original NDX calendar / aligned VIX; stop at the signal's US date.

    No new market fetches and no use of newer observations than the signal.
    Missing supplemental data must not suppress an otherwise valid Tripod signal.
    """
    latest, params = strategy['latest'], strategy['params']
    date = latest['date']
    if aligned is None:
        aligned = original_signal().align()
    dates, closes, vixes, filled = aligned
    if not (len(dates)==len(closes)==len(vixes)):
        return {'version':VERSION,'available':False,'date':date,'reason':'거래일·종가·VIX 길이 불일치'}
    try:
        i = dates.index(date)
    except ValueError:
        return {'version':VERSION, 'available':False, 'date':date, 'reason':'신호 기준일의 원자료 없음'}
    needed = max(*MA_WINDOWS, params['dd_lookback'], params['vix_ma_length'])
    if i + 1 < needed + 1:
        return {'version':VERSION, 'available':False, 'date':date, 'reason':'전일 비교에 필요한 이력 부족'}
    closes, vixes, dates = closes[:i+1], vixes[:i+1], dates[:i+1]
    if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in closes[-needed-1:]+vixes[-needed-1:]):
        return {'version':VERSION, 'available':False, 'date':date, 'reason':'유효하지 않은 보조 지표 원자료'}
    current, previous = closes[-1], closes[-2]
    sma = lambda a,n: sum(a[-n:])/n
    now = {str(n):sma(closes,n) for n in MA_WINDOWS}
    prior = {str(n):sma(closes[:-1],n) for n in MA_WINDOWS}
    pos = _position(current,[now[str(n)] for n in LONG_WINDOWS])
    prev_pos = _position(previous,[prior[str(n)] for n in LONG_WINDOWS])
    spread = now['50']/now['200']-1
    prev_spread = prior['50']/prior['200']-1
    relation = '상회' if spread>0 else '하회' if spread<0 else '동일'
    prev_relation = '상회' if prev_spread>0 else '하회' if prev_spread<0 else '동일'
    lookback=params['dd_lookback']
    window=closes[-lookback:];peak=max(window)
    peak_date=dates[-lookback:][max(j for j,x in enumerate(window) if x==peak)]
    dd=current/peak-1
    previous_peak=max(closes[:-1][-lookback:])
    prev_dd=previous/previous_peak-1
    vix_n=params['vix_ma_length']
    vix=sma(vixes,vix_n);prev_vix=sma(vixes[:-1],vix_n)
    def rnd(x):return round(x,6)
    return {'version':VERSION,'available':True,'date':date,'previous_date':dates[-2],
            'close':rnd(current),'moving_averages':{k:rnd(v) for k,v in now.items()},
            'close_vs_ma_pct':{k:rnd(100*(current/v-1)) for k,v in now.items()},
            'long_position':pos,'previous_long_position':prev_pos,
            'ma50_vs_ma200':relation,'previous_ma50_vs_ma200':prev_relation,'ma_spread_pct':rnd(100*spread),
            'drawdown_pct':rnd(100*dd),'drawdown_change_pp':rnd(100*(dd-prev_dd)),
            'previous_peak_close':rnd(previous_peak),'peak_changed':peak!=previous_peak,
            'peak_close':rnd(peak),'peak_date':peak_date,'peak_sessions':lookback,
            'vix10':rnd(vix),'vix_change':rnd(vix-prev_vix),'vix_sessions':vix_n,
            'vix_alignment':'원본 signal.align의 NDX 거래일 기준 · 과거 VIX 결측 전일 값 이월',
            'vix_alignment_total_filled_sessions':filled}


def describe(aux, strategy):
    if not aux['available']:
        return '<b>보조 지표</b>\n확인 불가: '+aux['reason']+' · 기존 신호는 별도 계산이에요.'
    ma=aux['moving_averages'];gaps=aux['close_vs_ma_pct']
    trend={'상단':'종가가 세 장기선 위에 있어 장기 가격 흐름은 상승 쪽이에요.',
           '하단':'종가가 세 장기선 아래에 있어 장기 가격 흐름은 약한 쪽이에요.',
           '구간 내·선 접촉':'종가가 장기선 구간 안이나 경계에 있어 방향 해석이 엇갈릴 수 있어요.'}[aux['long_position']]
    short={'상회':'50일 평균이 200일 평균보다 높아 중기 가격 흐름이 장기 평균보다 강해요.',
           '하회':'50일 평균이 200일 평균보다 낮아 중기 가격 흐름이 장기 평균보다 약해요.',
           '동일':'50일·200일 평균이 같아 두 평균의 방향 우위가 없어요.'}[aux['ma50_vs_ma200']]
    context = ''
    if aux['long_position']=='하단' and aux['ma50_vs_ma200']=='상회':
        context='지표가 엇갈려요. 평균선의 상승 배열이 남아 있어도 현재 종가의 약화는 별도로 봐야 해요.'
    elif aux['long_position']=='상단' and aux['ma50_vs_ma200']=='하회':
        context='지표가 엇갈려요. 현재 종가는 회복했지만 50/200일 평균의 약한 배열은 남아 있어요.'
    transitions=[]
    if aux['long_position']!=aux['previous_long_position']:
        transitions.append('장기선 위치 '+aux['previous_long_position']+' → '+aux['long_position'])
    if aux['ma50_vs_ma200']!=aux['previous_ma50_vs_ma200']:
        transitions.append('50/200일 관계 '+aux['previous_ma50_vs_ma200']+' → '+aux['ma50_vs_ma200'])
    delta=aux['drawdown_change_pp']
    draw='고점과의 거리가 줄었어요.' if delta>0 else '고점과의 거리가 늘어 조정이 깊어졌어요.' if delta<0 else '고점과의 거리는 전 거래일과 같아요.'
    change=aux['vix_change']
    fear='최근 변동성 경계가 커지는 흐름이에요.' if change>0 else '최근 변동성 경계가 완화되는 흐름이에요.' if change<0 else '최근 평균 변동성 수준이 같아요.'
    params=strategy['params'];up=strategy['latest']['regime']=='up'
    cutoff=params['vix_hot'] if up else params['vix_panic']
    line=(f"현재 {'상승' if up else '하락'} 상태의 기존 VIX 기준 {cutoff:g} "
          f"{'이상' if aux['vix10']>=cutoff else '미만'}이에요.")
    return ('<b>보조 지표 · 신호 조건 추가 없음</b>\n'
            f"기준 {aux['date']} / 전 거래일 {aux['previous_date']}\n\n"
            '<b>① 큰 흐름 · NDX 종가 / SMA(단순이동평균)</b>\n'
            f"종가 {aux['close']:,.2f}\n"+
            '\n'.join(f"SMA{n} {ma[str(n)]:,.2f} / 이격 {gaps[str(n)]:+.2f}%" for n in LONG_WINDOWS)+'\n'+
            f"SMA50 {ma['50']:,.2f} / SMA200 {ma['200']:,.2f}\n"
            f"50일선은 200일선을 {aux['ma50_vs_ma200']} (격차 {aux['ma_spread_pct']:+.2f}%)\n"
            f"해석: {trend} {short}\n"+
            (context+'\n' if context else '')+
            ('전일 변화: '+' / '.join(transitions)+'\n' if transitions else '전일 변화: 장기선 위치·50/200일 관계 유지\n')+
            '\n<b>② 고점에서 얼마나 빠졌나</b>\n'
            f"최근 {aux['peak_sessions']}거래일 최고 종가 {aux['peak_close']:,.2f} ({aux['peak_date']})\n"
            f"낙폭 {aux['drawdown_pct']:.2f}% / 전일 대비 {delta:+.2f}%p\n"
            f"해석: {draw} 낙폭만으로 바닥이나 반등을 확정할 수 없어요.\n"+
            ('고점 기준이 전일과 달라져 낙폭 변화에도 영향을 줄 수 있어요.\n' if aux['peak_changed'] else '')+
            '\n'
            f"<b>③ VIX {aux['vix_sessions']}일 평균</b>\n"
            f"{aux['vix10']:.2f} / 전일 대비 {change:+.2f}포인트\n"
            f"해석: {fear} {line}\n"
            'VIX는 NDX 방향 예측이 아니에요. 보조 해석만으로 매매 목표를 바꾸지 않아요.')
