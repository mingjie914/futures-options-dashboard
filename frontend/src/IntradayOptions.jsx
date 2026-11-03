import {memo,useMemo,useRef} from 'react';
import {ChartFrame,useChart} from './charts.js';
import {formatNumber as number,intradayRows,optionRows,optionSmile} from './intraday-live.js';

const IntradayTime=memo(function IntradayTime({data,previousClose}) {
  const ref=useRef(),day=useRef(),rows=useMemo(()=>intradayRows(data),[data]);
  const price=!!data?.future_code&&rows.some(row=>Number.isFinite(row.future_last)),iv=rows.some(row=>Number.isFinite(row.atm_iv)&&row.atm_iv>0),baseline=price&&Number.isFinite(previousClose)&&previousClose>0;
  const fields=[...(price?[['future_last',data.future_code.split('.')[0],'#505c6b']]:[]),...(iv?[['atm_iv','平值IV','#4e7fb9']]:[])];
  const value=(row,key)=>key==='atm_iv'?(Number.isFinite(row?.[key])&&row[key]>0?row[key]*100:null):row?.[key];
  const volume=price&&data.volume_unit==='contracts'&&rows.some(row=>Number.isFinite(row.future_volume)&&row.future_volume>=0),ready=fields.length>0;
  useChart(ref,chart=>{
    const reset=day.current&&day.current!==data.trade_date;day.current=data.trade_date;
    // Finite color intervals avoid empty gradient stops in ECharts 6.
    const prices=rows.map(row=>row.future_last).filter(Number.isFinite);
    const morning=rows.findLast(p=>p.time>='11:29'&&p.time<='11:30'&&Number.isFinite(p.future_last)),afternoon=rows.find(p=>p.time>='13:00'&&p.time<='13:01'&&Number.isFinite(p.future_last));
    chart.setOption({animation:false,grid:{left:12,right:65,top:18,bottom:28},
      tooltip:{trigger:'axis',confine:true,renderMode:'richText',axisPointer:{type:'cross',snap:true},formatter:params=>[data.trade_date+' '+params[0]?.axisValue,...params.map(p=>{const v=p.value?.[1];return p.seriesName+'：'+number(v)+(Number.isFinite(v)?p.seriesName==='平值IV'?'%':p.seriesName==='成交量'?' 手':baseline?' ('+number((v/previousClose-1)*100)+'%)':'':'');}),...(Number.isFinite(rows[params[0]?.dataIndex]?.strike)?['平值 '+number(rows[params[0].dataIndex].strike)]:[])].join('\n')},
      xAxis:{type:'category',boundaryGap:false,data:rows.map(p=>p.time),axisLabel:{interval:(_i,time)=>['09:30','10:30','11:30','14:00','15:00'].includes(time),formatter:time=>time==='11:30'?'11:30/13:00':time,fontSize:11}},
      yAxis:[{type:'value',scale:true,position:'right',show:price,splitNumber:5,axisLabel:{formatter:v=>number(v)},axisPointer:{label:{formatter:p=>number(p.value)}}},{type:'value',scale:true,position:'right',show:!price&&iv,min:v=>(v.min+v.max)/2-Math.max(1,v.max-v.min)*.6,max:v=>(v.min+v.max)/2+Math.max(1,v.max-v.min)*.6,axisLabel:{formatter:v=>number(v)+'%'},axisPointer:{show:false},splitLine:{show:false}},{type:'value',show:false,min:0,max:v=>Math.max(1,v.max)*8,axisPointer:{show:false},splitLine:{show:false}}],
      visualMap:baseline?[{type:'piecewise',show:false,dimension:1,seriesIndex:0,pieces:[{min:Math.min(previousClose,...prices)-1,max:previousClose,color:'#278570'},{min:previousClose,max:Math.max(previousClose,...prices)+1,color:'#bd514d'}]}]:[],
      dataZoom:[{type:'inside',filterMode:'none',...(reset?{start:0,end:100}:{})}],
      series:[...fields.map(([key,name,color])=>{
        const iv=key==='atm_iv',last=rows.findLast(row=>Number.isFinite(value(row,key))),latest=value(last,key),label=number(latest)+(iv?'%':''),ink=iv?color:baseline?(latest>=previousClose?'#bd514d':'#278570'):color;
        return {id:key,name,type:'line',showSymbol:false,connectNulls:false,yAxisIndex:iv?1:0,lineStyle:{width:1.7},itemStyle:{color},...(!iv?{areaStyle:{opacity:.045,origin:baseline?previousClose:'start'}}:{}),data:rows.map(row=>[row.time,value(row,key)]),
          markPoint:{silent:true,symbol:'circle',symbolSize:5,itemStyle:{color:ink},label:{show:false},data:last?[{coord:[last.time,latest]}]:[]},
          markLine:{silent:true,symbol:'none',lineStyle:{color:iv?'transparent':ink+'55',type:'dotted'},label:{show:true,position:'end',formatter:label,color:'#fff',backgroundColor:ink,padding:[2,4],fontSize:11},data:[...(last?[{yAxis:latest}]:[]),...(!iv&&baseline?[{yAxis:previousClose,label:{show:false},lineStyle:{color:'#aeb5bf',type:'dashed'}}]:[]),...(!iv&&morning&&afternoon?[[{coord:[morning.time,morning.future_last],label:{show:false},lineStyle:{color:'#7b8592',type:'dashed',width:1.3}},{coord:[afternoon.time,afternoon.future_last]}]]:[])]}};
      }),...(volume?[{id:'future_volume',name:'成交量',type:'bar',yAxisIndex:2,barWidth:'65%',silent:true,data:rows.map((row,i)=>({value:[row.time,Number.isFinite(row.future_volume)&&row.future_volume>=0?row.future_volume:null],itemStyle:{color:!Number.isFinite(row.future_last)||!Number.isFinite(rows[i-1]?.future_last)?'#aeb5bf80':row.future_last>=rows[i-1].future_last?'#bd514d80':'#27857080'}}))}]:[])]
    },{replaceMerge:['series','visualMap']});
  },[data,previousClose]);
  return <section><div className="observation-section-heading"><h3>分时</h3></div>{ready?<ChartFrame><div ref={ref} style={{height:320}} role="img" aria-label="期权日内价格与平值隐波"/></ChartFrame>:<p className="sub" style={{height:320,margin:0}}>等待分时数据</p>}</section>;
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
  return <section className="intraday-option-group"><div className="intraday-product-heading"><h2>{series}</h2><span className="sub">{Number.isInteger(meta?.trading_dte)&&meta.trading_dte>=0?meta.trading_dte:'—'} DTE / {meta?.last_trade_date||'—'}</span></div><div className="intraday-options"><IntradayTime data={intraday} previousClose={quotes.get(intraday?.future_code)?.prev_close}/><section><div className="observation-section-heading"><h3>波动率与持仓</h3></div>{ready?<ChartFrame><div ref={ref} style={{height:320}} role="img" aria-label="期权隐含波动率与持仓量"/></ChartFrame>:<p className="sub" style={{height:320,margin:0}}>等待报价</p>}</section></div></section>;
});
