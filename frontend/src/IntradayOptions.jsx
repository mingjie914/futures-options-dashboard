import {memo,useRef} from 'react';
import {ChartFrame,useChart} from './charts.js';
import {optionRows} from './intraday-live.js';

const number=v=>Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:2}):'—';
export default memo(function IntradayOptions({catalog,quotes}) {
  const ref=useRef(),rows=optionRows(catalog,quotes),meta=Object.values(catalog?.metadata||{}).find(m=>m.kind==='option');
  const spot=quotes.get(meta?.underlying)?.last;
  useChart(ref,chart=>chart.setOption({animation:false,
    legend:{top:0,left:'center',type:'scroll'},grid:{left:58,right:62,top:48,bottom:48},
    tooltip:{trigger:'axis',confine:true,renderMode:'richText',formatter:params=>['行权价 '+number(params[0]?.axisValue),...params.map(p=>{const value=Array.isArray(p.value)?p.value[1]:p.value;return p.seriesName+'：'+number(value)+(p.seriesName.includes('IV')&&Number.isFinite(value)?'%':'');})].join('\n')},
    xAxis:{type:'value',scale:true,axisLabel:{hideOverlap:true}},
    yAxis:[{type:'value',scale:true,axisLabel:{formatter:v=>v+'%'},splitLine:{lineStyle:{color:'#eef0f3'}}},{type:'value',min:0,splitLine:{show:false}}],
    dataZoom:[{type:'inside'},{type:'slider',height:18,bottom:4}],
    series:[...['C','P'].map((cp,i)=>({name:(i?'认沽':'认购')+'IV',type:'line',showSymbol:true,symbolSize:4,connectNulls:false,yAxisIndex:0,itemStyle:{color:i?'#a48b59':'#4e7fb9'},lineStyle:{width:2},z:4,data:rows.map(r=>[r.strike,Number.isFinite(r[cp]?.iv)&&r[cp].iv>0?r[cp].iv*100:null]),markLine:i||!Number.isFinite(spot)?undefined:{silent:true,symbol:'none',label:{formatter:'现货 '+number(spot)},lineStyle:{color:'#505c6b',type:'dashed'},data:[{xAxis:spot}]}})),
      ...['C','P'].map((cp,i)=>({name:(i?'认沽':'认购')+'持仓',type:'bar',yAxisIndex:1,barMaxWidth:8,itemStyle:{color:i?'#a48b5940':'#4e7fb940'},data:rows.map(r=>[r.strike,Number.isFinite(r[cp]?.oi)?r[cp].oi:null])}))]
  }),[catalog,quotes]);
  if(!rows.some(r=>['C','P'].some(cp=>Number.isFinite(r[cp]?.iv)||Number.isFinite(r[cp]?.oi))))return <p className="sub">等待期权实时行情接入</p>;
  return <section><div className="observation-section-heading"><h2>{catalog?.metadata?.[meta?.underlying]?.name||'期权'} · 波动率与持仓</h2><span className="sub">到期 {meta?.last_trade_date||'—'}</span></div>
    <ChartFrame><div ref={ref} style={{height:360}} role="img" aria-label="期权隐含波动率与持仓量"/></ChartFrame>
    <p className="sub">当日分时待接入</p>
  </section>;
});
