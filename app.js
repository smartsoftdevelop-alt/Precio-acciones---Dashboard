'use strict';

const $ = (sel, root=document) => root.querySelector(sel);
const state = {theme: localStorage.getItem('dashboard_theme') || 'dark'};

function nowText(){return new Intl.DateTimeFormat('es-PE',{dateStyle:'short',timeStyle:'medium'}).format(new Date())}
function markGlobal(){$('#globalUpdated').textContent=`Última acción: ${nowText()}`}
function setStatus(el,text,kind='neutral'){if(!el)return;el.textContent=text;el.className=`status ${kind}`}
function setTheme(theme){
  state.theme=theme;
  document.body.dataset.theme=theme;
  $('#themeBtn').textContent=`Tema: ${theme==='dark'?'oscuro':'claro'}`;
  localStorage.setItem('dashboard_theme',theme);
}
setTheme(state.theme);

function tvEmbed(target,src,config){
  const el=document.getElementById(target); if(!el)return;
  el.replaceChildren();
  const wrap=document.createElement('div');wrap.className='tradingview-widget-container';wrap.style.width='100%';wrap.style.height='100%';
  const inner=document.createElement('div');inner.className='tradingview-widget-container__widget';inner.style.width='100%';inner.style.height='100%';
  const script=document.createElement('script');script.async=true;script.src=src;script.textContent=JSON.stringify(config);
  wrap.append(inner,script);el.append(wrap);
}
function mini(target,symbol,range=$('#rangeSel').value){
  tvEmbed(target,'https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js',{
    symbol,locale:'es',dateRange:range,colorTheme:state.theme,isTransparent:false,autosize:true
  });
}
const stocks=[['nflx','NASDAQ:NFLX'],['uber','NYSE:UBER'],['hd','NYSE:HD'],['len','NYSE:LEN'],['unh','NYSE:UNH'],['pep','NASDAQ:PEP'],['aal','NASDAQ:AAL'],['mcd','NYSE:MCD']];
function renderPrice(){
  setStatus($('#precioStatus'),'Cargando…','loading');
  stocks.forEach(([id,symbol])=>mini(id,symbol));
  setStatus($('#precioStatus'),`TradingView embebido · ${nowText()}`,'neutral');
  markGlobal();
}

$('.tab').addEventListener('click',()=>document.querySelector('#precio').scrollIntoView({behavior:'smooth'}));
$('#themeBtn').addEventListener('click',()=>{setTheme(state.theme==='dark'?'light':'dark');renderPrice()});
$('#rangeSel').addEventListener('change',renderPrice);
$('#reloadBtn').addEventListener('click',renderPrice);
window.addEventListener('load',renderPrice);
