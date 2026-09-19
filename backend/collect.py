"""One explicit manual Wind query. Writes only this application's raw folder."""
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from WindPy import w


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);args=parser.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    def query(name,fn,*args):
        x=fn(*args);obj={k:getattr(x,k,None) for k in ['ErrorCode','Codes','Fields','Times','Data']}
        (out/(name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,default=str),encoding='utf-8')
        if x.ErrorCode:raise RuntimeError(name+': Wind error '+str(x.ErrorCode))
        return x
    start=w.start()
    if start.ErrorCode:raise RuntimeError('Wind connection failed '+str(start.ErrorCode))
    today=datetime.now().strftime('%Y-%m-%d')
    chain=query('chain',w.wset,'optionchain',f'date={today};us_code=000852.SH;option_var=all;call_put=all')
    records=[dict(zip(chain.Fields,r)) for r in zip(*chain.Data)]
    codes=[r['option_code'] for r in records]
    # Query all MO months; no per-page subscriptions, no production updater.
    # These are cumulative intraday fields in the same quote snapshot.  The
    # daily WSS volume/OI fields are not populated reliably before the close.
    quote_fields='rt_last,rt_bid1,rt_ask1,rt_bsize1,rt_asize1,rt_vol,rt_oi,rt_date,rt_time'
    quotes=query('quotes',w.wsq,','.join(codes),quote_fields)
    dates=[int(x) for x in quotes.Data[quotes.Fields.index('RT_DATE')] if x and str(x)!='nan']
    stamp=str(max(dates));day=stamp[:4]+'-'+stamp[4:6]+'-'+stamp[6:8]
    prior=query('previous_day',w.tdaysoffset,-1,day,'TradingCalendar=CFFEX').Data[0][0].strftime('%Y-%m-%d')
    # WSS expects the trade date in compact YYYYMMDD form.  Passing the
    # hyphenated form is accepted by the call but can silently return the
    # latest observation, which makes the previous-day OI equal to current OI.
    day_key=day.replace('-','');prior_key=prior.replace('-','')
    query('daily',w.wss,','.join(codes),'close,pre_close,volume,oi,amt,settle',f'tradeDate={day_key};priceAdj=U;cycle=D')
    query('previous',w.wss,','.join(codes),'close,oi',f'tradeDate={prior_key};priceAdj=U;cycle=D')
    spotq=query('spot',w.wsq,'000852.SH','rt_last,rt_date,rt_time')
    spot=spotq.Data[0][0]
    if int(spotq.Data[1][0])!=int(stamp):
        raise RuntimeError('指数与期权报价日期不一致，未发布截面')
    for month in sorted({str(r['month']) for r in records}):
        expiry=str(next(r['last_tradedate'] for r in records if str(r['month'])==month))[:10]
        nextday=(datetime.fromisoformat(day)+timedelta(days=1)).strftime('%Y-%m-%d')
        query('tdays_'+month[-4:],w.tdays,nextday,expiry,'TradingCalendar=CFFEX')
    now=datetime.now(timezone(timedelta(hours=8)))
    # After close/on a later calendar date value the archive at that day's close.
    is_live=day==today and now.hour<15
    asof=now.isoformat() if is_live else day+'T15:00:00+08:00'
    (out/'collection.json').write_text(json.dumps({'trade_date':day,'asof':asof,'received_at':now.isoformat(),
        'spot':spot,'mode':'live' if is_live else 'replay','previous_trade_date':prior},ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'folder':str(out),'contracts':len(codes),'trade_date':day}),flush=True)


if __name__=='__main__':main()
