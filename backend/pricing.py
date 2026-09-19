"""MO European forward pricing, amounts per actual contract multiplier."""
import math

RATE = .015
N = lambda x: .5 * (1 + math.erf(x / math.sqrt(2)))


def price(f, k, t, vol, cp, rate=RATE):
    if t <= 0:
        return max((f-k) * (1 if cp == 'C' else -1), 0)
    if min(f, k, vol) <= 0:
        raise ValueError('远期、执行价和波动率必须大于零')
    d1 = (math.log(f/k)+vol*vol*t/2)/(vol*math.sqrt(t))
    d2 = d1-vol*math.sqrt(t)
    sign = 1 if cp == 'C' else -1
    return math.exp(-rate*t)*sign*(f*N(sign*d1)-k*N(sign*d2))


def implied(p, f, k, t, cp):
    if p is None or t <= 0 or f <= 0:
        return None
    lo, hi = .00001, 5.
    if not price(f,k,t,lo,cp) < p < price(f,k,t,hi,cp):
        return None
    for _ in range(80):
        mid = (lo+hi)/2
        if price(f,k,t,mid,cp) > p:
            hi = mid
        else:
            lo = mid
    return (lo+hi)/2


def greeks(f,k,t,v,cp,multiplier):
    d = (math.log(f/k)+v*v*t/2)/(v*math.sqrt(t))
    density = math.exp(-d*d/2)/math.sqrt(2*math.pi)
    disc = math.exp(-RATE*t)
    return dict(delta=disc*(N(d)-(cp=='P')),
                gamma=disc*density/(f*v*math.sqrt(t)),
                vega=disc*f*density*math.sqrt(t)*.01*multiplier,
                theta=(price(f,k,max(0,t-1/365),v,cp)-price(f,k,t,v,cp))*multiplier)


def payoff_bounds(legs, cost):
    """All legs same expiry. Returns maximum loss/gain, None means unbounded."""
    def payoff(s):
        return sum(x['sign']*x['quantity']*x['multiplier']*max((s-x['strike'])*(1 if x['cp']=='C' else -1),0) for x in legs)-cost
    vals = [payoff(k) for k in {0,*[x['strike'] for x in legs]}]
    tail = sum(x['sign']*x['quantity']*x['multiplier'] for x in legs if x['cp']=='C')
    return (None if tail<0 else max(0,-min(vals))), (None if tail>0 else max(0,max(vals)))


def scenario(snapshot, legs, target, days, iv_shift_pp, fee_roundtrip=0., slippage_points=0., exit_spread_multiplier=1.):
    if min(fee_roundtrip,slippage_points)<0 or exit_spread_multiplier<1:
        raise ValueError('费用和滑点不能为负，退出价差倍数至少为1')
    lookup = {r['code']:r for r in snapshot['rows']}
    if not snapshot['spot']:
        raise ValueError('缺少同截面指数价格')
    selected=[]
    for leg in legs:
        row=lookup.get(leg['code'])
        if not row or row['iv'] is None or row['bid'] is None or row['ask'] is None:
            raise ValueError('所选合约缺少有效双边报价或可解IV：'+leg['code'])
        if row['bid'] <= 0 or row['ask'] < row['bid']:
            raise ValueError('报价不能用于退出估值')
        if days/365 > row['t']+1e-12:
            raise ValueError('退出时间超过某条腿到期，暂不支持跨到期处置')
        if row['iv']+iv_shift_pp/100 <= 0:
            raise ValueError('情景IV必须大于零')
        selected.append({**row,**leg,'sign':1 if leg['side']=='buy' else -1})
    cost=pnl=gross_pnl=fee_total=slippage_cost=0.
    detail=[]
    pricing_modes=set()
    for row in selected:
        sign=row['sign']; raw_entry=row['ask'] if sign>0 else row['bid']
        entry=max(0,raw_entry+sign*slippage_points)
        fee=fee_roundtrip*row['quantity']
        t=max(0,row['t']-days/365)
        if t<=1e-10:
            pricing_modes.add('expiry')
            intrinsic=(target-row['strike'])*(1 if row['cp']=='C' else -1)
            exit_price=max(0,intrinsic)
            raw_exit=exit_price
        else:
            pricing_modes.add('early_exit')
            f=row['forward']+target-snapshot['spot']
            if f<=0:raise ValueError('情景远期价格必须大于零')
            mid=price(f,row['strike'],t,row['iv']+iv_shift_pp/100,row['cp'])
            half=(row['ask']-row['bid'])/2
            raw_exit=max(0,mid-sign*half)
            exit_price=max(0,mid-sign*(half*exit_spread_multiplier+slippage_points))
        amount=sign*row['quantity']*row['multiplier']
        cost+=amount*entry+fee; p=amount*(exit_price-entry)-fee;pnl+=p
        gross_pnl+=amount*(raw_exit-raw_entry);fee_total+=fee
        slippage_cost+=amount*(entry-raw_entry)
        detail.append({'code':row['code'],'entry':entry,'exit':exit_price,'pnl':p,'fee':fee})
    same=len({r['month'] for r in selected})==1
    loss,gain=payoff_bounds(selected,cost) if same else (None,None)
    if pricing_modes=={'expiry'}:
        valuation_warning='到期结算：以目标标的价格直接计算内在价值，不使用固定基差或退出半价差'
    elif pricing_modes=={'early_exit'}:
        valuation_warning='提前退出：固定基差、各腿IV平移；退出沿用当前半价差，非保证可成交'
    else:
        valuation_warning='混合退出：到期腿按目标标的内在价值结算，提前退出腿固定基差并沿用当前半价差'
    fee_warning='费用按用户填写的每腿每手全程总额预留；滑点为单边期权价格点数' if fee_roundtrip or slippage_points else '费用/滑点设为0，尚未计入额外交易成本'
    return dict(snapshot_id=snapshot['snapshot_id'],cost=cost,max_loss=loss,max_gain=gain,pnl=pnl,
                gross_pnl=gross_pnl,friction_cost=gross_pnl-pnl,fee_total=fee_total,slippage_cost=slippage_cost,
                risk_status='same_expiry' if same else 'cross_expiry_unresolved',fees_included=fee_roundtrip>0,legs=detail,
                warnings=[fee_warning, valuation_warning,
                          '利率假设1.5%；目标价格为情景结算标的，非盘中触价保证']+([] if same else ['跨期生命周期最大损益依赖到期处置，未给出上限']))


def exit_analysis(snapshot, legs, price_min, price_max, price_steps, horizon_days, time_steps,
                  iv_shift_pp, target, profit_target, fee_roundtrip=0., slippage_points=0.,
                  stress_spread_multiplier=2.):
    """Bounded grid, constant-IV conditional exits; never a first-touch probability."""
    if price_max<=price_min:raise ValueError('价格上限必须高于下限')
    if horizon_days<0:raise ValueError('持有天数不能为负')
    lookup={r['code']:r for r in snapshot['rows']}
    if any(leg['code'] not in lookup for leg in legs):raise ValueError('所选截面没有该组合的全部合约')
    max_days=min(lookup[leg['code']]['t']*365 for leg in legs)
    if horizon_days>max_days+1e-9:raise ValueError('分析时间超过最早到期腿，请缩短持有时间')
    prices=[price_min+(price_max-price_min)*i/(price_steps-1) for i in range(price_steps)]
    days=[horizon_days*i/(time_steps-1) for i in range(time_steps)] if horizon_days else [0.]
    def evaluate(price_value,day,multiplier):
        return scenario(snapshot,legs,price_value,day,iv_shift_pp,fee_roundtrip,slippage_points,multiplier)
    # Reject unusable strategies rather than making an all-empty grid look successful.
    entry=evaluate(snapshot['spot'],0,1.)
    cells=[];target_rows=[];reverse_rows=[]
    for di,day in enumerate(days):
        first_normal=first_stress=None
        for pi,price_value in enumerate(prices):
            try:
                normal=evaluate(price_value,day,1.)
                stress=evaluate(price_value,day,stress_spread_multiplier)
                cells.append(dict(price_index=pi,day_index=di,pnl=normal['pnl'],stress_pnl=stress['pnl']))
                if first_normal is None and normal['pnl']>=profit_target:first_normal=price_value
                if first_stress is None and stress['pnl']>=profit_target:first_stress=price_value
            except ValueError as exc:
                cells.append(dict(price_index=pi,day_index=di,pnl=None,stress_pnl=None,error=str(exc)))
        try:
            target_rows.append(dict(days=day,normal=evaluate(target,day,1.),stress=evaluate(target,day,stress_spread_multiplier)))
        except ValueError as exc:target_rows.append(dict(days=day,normal=None,stress=None,error=str(exc)))
        reverse_rows.append(dict(days=day,normal_price=first_normal,stress_price=first_stress))
    return dict(snapshot_id=snapshot['snapshot_id'],calculation_version='exit-v1',asof=snapshot['asof'],prices=prices,days=days,cells=cells,
                target_rows=target_rows,reverse_rows=reverse_rows,
                entry={k:entry[k] for k in ('cost','max_loss','max_gain','fee_total','slippage_cost','risk_status')},
                warnings=['条件情景，不是触及目标概率；IV按输入平移，提前退出保持当前基差',
                          '普通退出沿用当前半价差，保守退出放大半价差；两者均另计用户滑点和全程费用',
                          '到期直接按目标结算标的内在价值估值，不加退出价差或退出滑点；全程费用仍按用户预算预留',
                          '反查结果为价格网格从低到高首个达标点；非精确根，不保证整个区间或更高价格持续达标',
                          '最大损益为到期口径，跨到期组合未给出上限；模型报价不保证实际成交'])
