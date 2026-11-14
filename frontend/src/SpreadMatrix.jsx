import './SpreadMatrix.css';
import {useMemo} from 'react';
import {formatPrice} from './charts.js';
const fmt=v=>formatPrice(v);

export function spreadMatrix(contracts, asOf, terms) {
 const nodes=contracts.filter(c=>c.trade_date===asOf&&/^[A-Z]+\d{4}$/.test(c.symbol)&&Number(c.symbol.slice(-2))>=1&&Number(c.symbol.slice(-2))<=12).sort((a,b)=>a.symbol.localeCompare(b.symbol));
 const dates=new Map((terms?.as_of===asOf?terms.contracts||[]:[]).filter(c=>['rds_recorded','wind_recorded'].includes(c.status)&&/^\d{4}-\d{2}-\d{2}$/.test(c.last_trade_date)).map(c=>[c.symbol,Date.parse(c.last_trade_date)]));
 let minimum=Infinity,maximum=-Infinity;
 const rows=nodes.map((row,i)=>({symbol:row.symbol,cells:nodes.map((_,j)=>{
  const near=nodes[Math.min(i,j)],far=nodes[Math.max(i,j)];
  const spread=Number.isFinite(near.close)&&Number.isFinite(far.close)?near.close-far.close:null;
  const gap=(dates.get(far.symbol)-dates.get(near.symbol))/86400000,days=Number.isFinite(gap)&&gap>0?gap:null;
  const value=i===j?(Number.isFinite(row.close)?row.close:null):i<j?spread:days>0&&spread!=null?spread/days:null;
  if(i>j&&Number.isFinite(value)){minimum=Math.min(minimum,value);maximum=Math.max(maximum,value);}
  return {near:near.symbol,far:far.symbol,days,value};
 })}));
 rows.forEach((row,i)=>row.cells.forEach((cell,j)=>{if(i>j&&Number.isFinite(cell.value)){cell.maximum=Math.abs(cell.value-maximum)<1e-10;cell.minimum=Math.abs(cell.value-minimum)<1e-10;}}));
 return rows;
}

export function SpreadMatrix({data,onPair,title='跨期矩阵'}) {
 const rows=useMemo(()=>spreadMatrix(data.contracts,data.meta.as_of,data.contract_terms),[data.contracts,data.meta.as_of,data.contract_terms]);
 if(!rows.length)return null;
 return <section className="spread-matrix" aria-label="跨期价差矩阵">
  {title&&<h2>{title}</h2>}
  <div className="table-wrap" tabIndex={0} role="region" aria-label={(title||rows[0].symbol.replace(/\d.*$/,'')+'跨期价差矩阵')+'，可横向滚动'}><table style={{'--matrix-width':`${64+rows.length*72}px`}}><colgroup><col style={{width:64}}/>{rows.map(row=><col key={row.symbol}/>)}</colgroup><thead><tr><th scope="col">合约</th>{rows.map(row=><th scope="col" key={row.symbol}>{row.symbol}</th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={row.symbol}><th scope="row">{row.symbol}</th>{row.cells.map((cell,j)=>{
   const diagonal=i===j,selected=!diagonal&&cell.near===data.near&&cell.far===data.far,mark=[cell.maximum?'多':'',cell.minimum?'空':''].filter(Boolean).join('/'),Cell=onPair?'button':'span';
   return <td key={rows[j].symbol} className={(diagonal?'matrix-price':i<j?'matrix-spread':'matrix-daily')+(selected?' matrix-selected':'')}>
    {diagonal?<span>{fmt(cell.value)}</span>:<Cell type={onPair?'button':undefined} className={mark?'matrix-extreme':undefined} aria-pressed={onPair?selected:undefined} aria-label={`${cell.near} − ${cell.far}，${i<j?'价差':'日均'} ${fmt(cell.value)}${mark?'，'+mark+'头移仓参考':''}${onPair?'，选择组合':''}`} onClick={onPair?()=>onPair(cell.near,cell.far):undefined} title={`${cell.near} − ${cell.far}${mark?`；${mark}头移仓参考`:""}`}>
     {mark&&<span className="matrix-roll-mark" aria-hidden="true">{cell.maximum&&<span className="direction-long">多</span>}{cell.minimum&&<span className="direction-short">空</span>}</span>}<strong className={cell.value>0?'direction-long':cell.value<0?'direction-short':''}>{fmt(cell.value)}</strong>{i>j&&<small>{cell.days==null?'—':`${cell.days}天`}</small>}
    </Cell>}
   </td>;
  })}</tr>)}</tbody></table></div>
 </section>;
}

