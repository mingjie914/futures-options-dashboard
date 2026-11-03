import {memo,useMemo,useRef} from 'react';
import {ChartFrame,useChart} from './charts.js';
import {formatNumber as number,intradayRows,optionRows,optionSmile} from './intraday-live.js';

const IntradayTime=memo(function IntradayTime({data}) {
  const ref=useRef(),rows=useMemo(()=>intradayRows(data),[data]);
  useChart(ref,chart=>chart.setOption({animation:false,legend:{top:0,left:'center'},grid:{left:58,right:52,top:40,bottom:46},
    tooltip:{trigger:'axis',confine:true,renderMode:'richText',formatter:params=>[data.trade_date+' '+params[0]?.axisValue,...params.map(p=>p.seriesName+'：'+number(p.value)+(p.seriesName==='平值IV'&&Number.isFinite(p.value)?'%':'')),...(Number.isFinite(rows[params[0]?.dataIndex]?.strike)?['平值 '+number(rows[params[0].dataIndex].strike)]:[])].join('\n')},
    xAxis:{type:'category',boundaryGap:false,data:rows.map(p=>p.time),axisLabel:{hideOverlap:true}},
    yAxis:[{type:'value',scale:true,show:!!data?.future_code},{type:'value',scale:true,axisLabel:{formatter:v=>v+'%'},splitLine:{show:false}}],
    dataZoom:[{type:'inside'},{type:'slider',height:18,bottom:4}],
    series:[...(data?.future_code?[{id:'price',name:data.future_code.split('.')[0],type:'line',showSymbol:false,connectNulls:false,lineStyle:{width:2},itemStyle:{color:'#4e7fb9'},data:rows.map(p=>p.future_last)}]:[]),
      {id:'iv',name:'平值IV',type:'line',showSymbol:false,connectNulls:false,yAxisIndex:1,lineStyle:{width:2},itemStyle:{color:'#a48b59'},data:rows.map(p=>Number.isFinite(p.atm_iv)&&p.atm_iv>0?p.atm_iv*100:null)}]
  },{replaceMerge:['series']}),[data]);
  return <section><div className="observation-section-heading"><h3>{data&&!data.future_code?'平值隐波':'价格与平值隐波'}</h3></div>{rows.length?<ChartFrame><div ref={ref} style={{height:320}} role="img" aria-label="期权日内价格与平值隐波"/></ChartFrame>:<p className="sub" style={{height:345,margin:0}}>当日分时待接入</p>}</section>;
});

export default memo(function IntradayOptions({catalog,quotes,series,intraday}) {
  const selected=useMemo(()=>({...catalog,codes:(catalog?.codes||[]).filter(code=>code.startsWith(series+'-'))}),[catalog,series]);
  const ref=useRef(),rows=optionRows(selected,quotes),meta=catalog?.metadata?.[rows[0]?.C?.code||rows[0]?.P?.code];
  const spot=quotes.get(meta?.underlying)?.last;
  useChart(ref,chart=>chart.setOption({animation:false,
    legend:{top:0,left:'center',type:'scroll'},grid:{left:58,right:62,top:48,bottom:48},
    tooltip:{trigger:'axis',confine:true,renderMode:'richText',formatter:params=>['行权价 '+number(params[0]?.axisValue),...params.map(p=>{const value=Array.isArray(p.value)?p.value[1]:p.value;return p.seriesName+'：'+number(value)+(p.seriesName==='隐波'&&Number.isFinite(value)?'%':'');})].join('\n')},
    xAxis:{type:'value',scale:true,axisLabel:{hideOverlap:true}},
    yAxis:[{type:'value',scale:true,axisLabel:{formatter:v=>v+'%'},splitLine:{lineStyle:{color:'#eef0f3'}}},{type:'value',min:0,splitLine:{show:false}}],
    dataZoom:[{type:'inside'},{type:'slider',height:18,bottom:4}],
    series:[{id:'iv',name:'隐波',type:'line',showSymbol:true,symbolSize:4,connectNulls:false,itemStyle:{color:'#4e7fb9'},lineStyle:{width:2},z:4,data:optionSmile(rows,spot).map(([k,iv])=>[k,iv===null?null:iv*100]),markLine:{silent:true,symbol:'none',label:{formatter:'现货 '+number(spot)},lineStyle:{color:'#505c6b',type:'dashed'},data:Number.isFinite(spot)?[{xAxis:spot}]:[]}},
      ...['C','P'].map((cp,i)=>({id:cp+'oi',name:(i?'认沽':'认购')+'持仓',type:'bar',yAxisIndex:1,barMaxWidth:8,itemStyle:{color:i?'#a48b5940':'#4e7fb940'},data:rows.map(r=>[r.strike,Number.isFinite(r[cp]?.oi)?r[cp].oi:null])}))]
  },{replaceMerge:['series']}),[catalog,series,spot,...rows.flatMap(r=>[r.strike,r.C?.iv,r.C?.oi,r.P?.iv,r.P?.oi])]);
  const ready=rows.some(r=>['C','P'].some(cp=>Number.isFinite(r[cp]?.iv)||Number.isFinite(r[cp]?.oi)));
  return <section className="intraday-option-group"><div className="intraday-product-heading"><h2>{series}</h2><span className="sub">{Number.isInteger(meta?.trading_dte)&&meta.trading_dte>=0?meta.trading_dte:'—'} DTE / {meta?.last_trade_date||'—'}</span></div><div className="intraday-options"><IntradayTime data={intraday}/><section><div className="observation-section-heading"><h3>波动率与持仓</h3></div>{ready?<ChartFrame><div ref={ref} style={{height:320}} role="img" aria-label="期权隐含波动率与持仓量"/></ChartFrame>:<p className="sub" style={{height:345,margin:0}}>等待期权实时行情接入</p>}</section></div></section>;
});
