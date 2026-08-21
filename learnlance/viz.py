"""Render the knowledge graph as a single self-contained, offline HTML file.
No CDNs: a small vanilla-JS force-directed layout is embedded so the file works
anywhere, forever."""
from __future__ import annotations

import json
from pathlib import Path

from . import config

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>learnlance — what you've learned</title>
<style>
  :root{
    --bg:#0f1117; --panel:#171a23; --panel2:#1e222d; --line:#2a2f3d;
    --fg:#e7e9ee; --muted:#98a0b3; --accent:#6ea8fe;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  #wrap{display:flex;height:100%}
  #graph{flex:1;position:relative;overflow:hidden}
  svg{width:100%;height:100%;display:block;cursor:grab}
  svg.dragging{cursor:grabbing}
  #side{width:340px;background:var(--panel);border-left:1px solid var(--line);
    padding:18px;overflow:auto}
  h1{font-size:16px;margin:0 0 2px}
  .sub{color:var(--muted);font-size:12px;margin-bottom:14px}
  .stat{display:flex;gap:14px;margin-bottom:14px}
  .stat div{background:var(--panel2);border:1px solid var(--line);border-radius:10px;
    padding:8px 12px;flex:1;text-align:center}
  .stat b{display:block;font-size:20px}
  .stat span{color:var(--muted);font-size:11px}
  input[type=search]{width:100%;padding:9px 11px;border-radius:9px;border:1px solid var(--line);
    background:var(--panel2);color:var(--fg);margin-bottom:12px;font-size:13px}
  .legend{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}
  .legend span{font-size:11px;color:var(--muted);display:flex;align-items:center;gap:5px}
  .dot{width:10px;height:10px;border-radius:50%;display:inline-block}
  #detail{background:var(--panel2);border:1px solid var(--line);border-radius:12px;
    padding:14px;min-height:120px}
  #detail h2{font-size:15px;margin:0 0 6px}
  #detail .tag{display:inline-block;font-size:10px;text-transform:uppercase;letter-spacing:.5px;
    padding:2px 8px;border-radius:20px;background:#2b3550;color:var(--accent);margin-right:6px}
  #detail p{color:#d6dae4;margin:8px 0}
  #detail .ex{border-top:1px solid var(--line);margin-top:10px;padding-top:10px;color:var(--muted);font-size:12px}
  #detail .ex b{color:#cfd6e6}
  #detail .chips{margin:10px 0 2px;display:flex;flex-wrap:wrap;gap:6px}
  #detail .chip{font-size:11px;color:#8fd0dd;background:#123038;border:1px solid #1c4550;
    padding:2px 8px;border-radius:20px}
  .empty{color:var(--muted)}
  node text{pointer-events:none}
  .hint{position:absolute;bottom:10px;left:12px;color:var(--muted);font-size:11px;opacity:.7}
</style>
</head>
<body>
<div id="wrap">
  <div id="graph">
    <svg id="svg"></svg>
    <div class="hint">drag nodes • scroll to zoom • click a concept</div>
  </div>
  <div id="side">
    <h1>learnlance</h1>
    <div class="sub">concepts you've picked up while coding with Claude</div>
    <div class="stat">
      <div><b id="s-topics">0</b><span>CONCEPTS</span></div>
      <div><b id="s-links">0</b><span>LINKS</span></div>
      <div><b id="s-turns">0</b><span>TURNS</span></div>
    </div>
    <input type="search" id="q" placeholder="Search concepts…" autocomplete="off">
    <div class="legend" id="legend"></div>
    <div id="detail"><span class="empty">Click any concept to see what it is and where you met it.</span></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const CATCOLORS = {
  "algorithm":"#6ea8fe","data-structure":"#63e6be","language-feature":"#ffd43b",
  "pattern":"#da77f2","api":"#ff922b","security":"#ff6b6b","testing":"#4dabf7",
  "tooling":"#a9e34b","architecture":"#f783ac","math":"#38d9a9","domain":"#e599f7",
  "other":"#8d99b3"
};
const color = c => CATCOLORS_get(c);
function CATCOLORS_get(c){ return CATCOLORS[c] || CATCOLORS.other; }

const nodesMap = DATA.nodes || {};
const nodes = Object.values(nodesMap).map(n => ({...n,
  x: (Math.random()-0.5)*600, y:(Math.random()-0.5)*400, vx:0, vy:0}));
const idIndex = {}; nodes.forEach((n,i)=>idIndex[n.id]=n);
const links = (DATA.edges||[]).filter(e=>idIndex[e.source]&&idIndex[e.target])
  .map(e=>({source:idIndex[e.source], target:idIndex[e.target], type:e.type}));

document.getElementById('s-topics').textContent = nodes.filter(n=>!n.placeholder).length;
document.getElementById('s-links').textContent = links.length;
document.getElementById('s-turns').textContent = (DATA.meta&&DATA.meta.turns)||0;

// Legend (only categories present)
const cats = [...new Set(nodes.map(n=>n.category))];
document.getElementById('legend').innerHTML = cats.map(c=>
  `<span><i class="dot" style="background:${color(c)}"></i>${c}</span>`).join('');

const svg = document.getElementById('svg');
const NS="http://www.w3.org/2000/svg";
let W=svg.clientWidth, H=svg.clientHeight;
const view = {x:W/2, y:H/2, k:1};
const gRoot = document.createElementNS(NS,'g'); svg.appendChild(gRoot);
const gLinks = document.createElementNS(NS,'g'); gRoot.appendChild(gLinks);
const gNodes = document.createElementNS(NS,'g'); gRoot.appendChild(gNodes);

function radius(n){ return 7 + Math.sqrt(n.count||1)*4; }

const EDGECOLOR = {"co-occurs":"#5b6a99","related":"#40507a","shared-tag":"#2f6d7a"};
const linkEls = links.map(l=>{
  const el=document.createElementNS(NS,'line');
  el.setAttribute('stroke', EDGECOLOR[l.type] || "#333a4d");
  el.setAttribute('stroke-width', (1 + Math.min(l.weight||1,6)*0.35).toFixed(2));
  el.setAttribute('stroke-opacity', l.type==='shared-tag' ? 0.55 : 0.8);
  const title=document.createElementNS(NS,'title');
  title.textContent = l.type + (l.tags&&l.tags.length?(' · '+l.tags.join(', ')):'') + ' ×'+(l.weight||1);
  el.appendChild(title);
  gLinks.appendChild(el); return el;
});

const nodeEls = nodes.map(n=>{
  const g=document.createElementNS(NS,'g'); g.style.cursor='pointer';
  const c=document.createElementNS(NS,'circle');
  c.setAttribute('r', radius(n));
  c.setAttribute('fill', color(n.category));
  c.setAttribute('fill-opacity', n.placeholder?0.35:0.92);
  c.setAttribute('stroke', '#0b0d12'); c.setAttribute('stroke-width',1.5);
  const t=document.createElementNS(NS,'text');
  t.textContent=n.name; t.setAttribute('font-size',11);
  t.setAttribute('fill', n.placeholder?'#7b849b':'#dfe3ec');
  t.setAttribute('x', radius(n)+4); t.setAttribute('y',4);
  g.appendChild(c); g.appendChild(t); gNodes.appendChild(g);
  g.addEventListener('click',(e)=>{e.stopPropagation(); showDetail(n);});
  n._c=c; n._g=g;
  return g;
});

// ---- force simulation ----
let alpha=1;
function tick(){
  alpha *= 0.985; if(alpha<0.02) alpha=0.02;
  // repulsion
  for(let i=0;i<nodes.length;i++){
    for(let j=i+1;j<nodes.length;j++){
      const a=nodes[i], b=nodes[j];
      let dx=a.x-b.x, dy=a.y-b.y; let d2=dx*dx+dy*dy||0.01;
      const f = 2600/d2 * alpha;
      const d=Math.sqrt(d2); const fx=dx/d*f, fy=dy/d*f;
      a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy;
    }
  }
  // springs
  for(const l of links){
    const a=l.source,b=l.target;
    let dx=b.x-a.x, dy=b.y-a.y; let d=Math.sqrt(dx*dx+dy*dy)||0.01;
    const target=90; const f=(d-target)*0.02*alpha;
    const fx=dx/d*f, fy=dy/d*f;
    a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy;
  }
  // centering + integrate
  for(const n of nodes){
    n.vx += (-n.x)*0.0015*alpha; n.vy += (-n.y)*0.0015*alpha;
    if(n===dragged) continue;
    n.vx*=0.86; n.vy*=0.86;
    n.x+=n.vx; n.y+=n.vy;
  }
  render();
  requestAnimationFrame(tick);
}
function render(){
  gRoot.setAttribute('transform',`translate(${view.x},${view.y}) scale(${view.k})`);
  for(let i=0;i<links.length;i++){
    const l=links[i], el=linkEls[i];
    el.setAttribute('x1',l.source.x); el.setAttribute('y1',l.source.y);
    el.setAttribute('x2',l.target.x); el.setAttribute('y2',l.target.y);
  }
  for(let i=0;i<nodes.length;i++){
    nodeEls[i].setAttribute('transform',`translate(${nodes[i].x},${nodes[i].y})`);
  }
}

// ---- interaction: pan / zoom / drag ----
let dragged=null, dragOff={x:0,y:0}, panning=false, panStart=null;
function toWorld(px,py){ return {x:(px-view.x)/view.k, y:(py-view.y)/view.k}; }
nodes.forEach((n,i)=>{
  nodeEls[i].addEventListener('mousedown',(e)=>{
    e.stopPropagation(); dragged=n; alpha=0.5;
    const w=toWorld(e.offsetX,e.offsetY); dragOff={x:w.x-n.x,y:w.y-n.y};
  });
});
svg.addEventListener('mousedown',(e)=>{ panning=true; userMoved=true; panStart={x:e.offsetX-view.x,y:e.offsetY-view.y}; svg.classList.add('dragging');});
window.addEventListener('mousemove',(e)=>{
  const rect=svg.getBoundingClientRect(); const ox=e.clientX-rect.left, oy=e.clientY-rect.top;
  if(dragged){ const w=toWorld(ox,oy); dragged.x=w.x-dragOff.x; dragged.y=w.y-dragOff.y; dragged.vx=0; dragged.vy=0; alpha=Math.max(alpha,0.3);}
  else if(panning){ view.x=ox-panStart.x; view.y=oy-panStart.y; }
});
window.addEventListener('mouseup',()=>{ dragged=null; panning=false; svg.classList.remove('dragging');});
svg.addEventListener('wheel',(e)=>{
  e.preventDefault(); userMoved=true; const rect=svg.getBoundingClientRect();
  const ox=e.clientX-rect.left, oy=e.clientY-rect.top;
  const before=toWorld(ox,oy); const factor=e.deltaY<0?1.1:0.9;
  view.k=Math.max(0.2,Math.min(4,view.k*factor));
  const after=toWorld(ox,oy);
  view.x+=(after.x-before.x)*view.k; view.y+=(after.y-before.y)*view.k;
},{passive:false});
let userMoved=false;
window.addEventListener('resize',()=>{
  W=svg.clientWidth; H=svg.clientHeight;
  if(!userMoved){ view.x=W/2; view.y=H/2; }  // keep graph centered until the user pans/zooms
});

// ---- detail panel + search ----
function esc(s){ return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function showDetail(n){
  const ex=(n.examples||[]).slice().reverse().map(e=>
    `<div class="ex"><b>${esc(e.did||'')}</b><br>${esc(e.why_here||'')}`+
    (e.files&&e.files.length?`<br><span style="opacity:.7">${esc(e.files.join(', '))}</span>`:'')+`</div>`).join('');
  const tags=(n.tags||[]).map(t=>`<span class="chip">#${esc(t)}</span>`).join('');
  const conns=links.filter(l=>l.source===n||l.target===n).length;
  document.getElementById('detail').innerHTML =
    `<h2>${esc(n.name)}</h2>`+
    `<span class="tag">${esc(n.category)}</span>`+(n.level?`<span class="tag">${esc(n.level)}</span>`:'')+
    `<span class="tag">seen ${n.count||0}×</span>`+`<span class="tag">${conns} links</span>`+
    `<p>${esc(n.explanation)||'<span class="empty">A related concept — no explanation captured yet.</span>'}</p>`+
    (tags?`<div class="chips">${tags}</div>`:'')+
    ex;
  nodes.forEach(m=>m._c.setAttribute('stroke','#0b0d12'));
  n._c.setAttribute('stroke','#fff'); n._c.setAttribute('stroke-width',2.5);
}
document.getElementById('q').addEventListener('input',(e)=>{
  const q=e.target.value.toLowerCase();
  nodes.forEach(n=>{
    const hit=!q || n.name.toLowerCase().includes(q);
    n._g.style.opacity = hit?1:0.12;
  });
});
svg.addEventListener('click',()=>{ /* background click keeps last detail */ });

requestAnimationFrame(tick);
if(nodes.length===0){
  document.getElementById('detail').innerHTML='<span class="empty">Nothing learned yet. Let Claude Code generate some code with the hook installed, then refresh.</span>';
}
</script>
</body>
</html>
"""


def render_html(graph: dict, path: Path | None = None) -> Path:
    path = path or config.HTML_PATH
    config.ensure_home()
    html = _TEMPLATE.replace("__DATA__", json.dumps(graph))
    path.write_text(html, encoding="utf-8")
    return path
