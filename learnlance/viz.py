"""Render the knowledge graph as a single self-contained, offline HTML file.
No CDNs: a small vanilla-JS force-directed layout is embedded so the file works
anywhere, forever.

Supports per-project graphs with a navigation panel to switch between projects.
"""
from __future__ import annotations

import json
import os
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
    --nav-w:220px;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  #app{display:flex;height:100%}

  /* --- project nav panel --- */
  #nav{width:var(--nav-w);background:var(--panel);border-right:1px solid var(--line);
    display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
  #nav-header{padding:14px 16px 10px;border-bottom:1px solid var(--line)}
  #nav-header h1{font-size:14px;margin:0;color:var(--accent);letter-spacing:.5px}
  #nav-header .sub{font-size:11px;color:var(--muted);margin-top:2px}
  #project-list{flex:1;overflow-y:auto;padding:8px 0}
  .proj-item{display:flex;align-items:center;gap:10px;padding:10px 16px;cursor:pointer;
    border-left:3px solid transparent;transition:background .15s}
  .proj-item:hover{background:var(--panel2)}
  .proj-item.active{background:var(--panel2);border-left-color:var(--accent)}
  .proj-icon{width:32px;height:32px;border-radius:8px;background:var(--panel2);
    border:1px solid var(--line);display:flex;align-items:center;justify-content:center;
    font-size:14px;flex-shrink:0}
  .proj-item.active .proj-icon{background:#1a2744;border-color:var(--accent)}
  .proj-name{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .proj-meta{font-size:11px;color:var(--muted)}
  .proj-info{min-width:0}
  #nav-footer{padding:10px 16px;border-top:1px solid var(--line);font-size:11px;color:var(--muted)}

  /* --- main content area --- */
  #wrap{display:flex;flex:1;height:100%;overflow:hidden}
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
  .controls{background:var(--panel2);border:1px solid var(--line);border-radius:10px;
    padding:10px 12px;margin-bottom:14px;display:flex;flex-direction:column;gap:8px}
  .controls label{color:var(--muted);font-size:12px;display:flex;align-items:center;gap:8px}
  .controls input[type=range]{flex:1;accent-color:var(--accent)}
  .controls .val{color:var(--fg);min-width:12px;text-align:right}
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
<div id="app">
  <div id="nav">
    <div id="nav-header">
      <h1>learnlance</h1>
      <div class="sub">your projects</div>
    </div>
    <div id="project-list"></div>
    <div id="nav-footer"></div>
  </div>
  <div id="wrap">
    <div id="graph">
      <svg id="svg"></svg>
      <div class="hint">drag nodes • scroll to zoom • click a concept</div>
    </div>
    <div id="side">
      <h1 id="proj-title">learnlance</h1>
      <div class="sub" id="proj-subtitle">select a project to view its knowledge graph</div>
      <div class="stat">
        <div><b id="s-topics">0</b><span>CONCEPTS</span></div>
        <div><b id="s-links">0</b><span>LINKS</span></div>
        <div><b id="s-turns">0</b><span>TURNS</span></div>
      </div>
      <input type="search" id="q" placeholder="Search concepts…" autocomplete="off">
      <div class="controls">
        <label><input type="checkbox" id="showRelated"> show related concepts (dimmed)</label>
        <label>min link strength
          <input type="range" id="minW" min="1" max="6" step="1" value="1">
          <span class="val" id="minWv">1</span>
        </label>
      </div>
      <div class="legend" id="legend"></div>
      <div id="detail"><span class="empty">Click any concept to see what it is and where you met it.</span></div>
    </div>
  </div>
</div>
<script>
// ALL_PROJECTS: { slug: { name, path, concepts, turns } }
const ALL_PROJECTS = __PROJECTS__;
// GRAPHS: { slug: graphData }
const GRAPHS = __GRAPHS__;
// CURRENT: the slug of the initially-selected project
let CURRENT = __CURRENT__;

const CATCOLORS = {
  "algorithm":"#6ea8fe","data-structure":"#63e6be","language-feature":"#ffd43b",
  "pattern":"#da77f2","api":"#ff922b","security":"#ff6b6b","testing":"#4dabf7",
  "tooling":"#a9e34b","architecture":"#f783ac","math":"#38d9a9","domain":"#e599f7",
  "other":"#8d99b3"
};
const color = c => CATCOLORS[c] || CATCOLORS.other;

let DATA, rawNodes, rawEdges;
const filters = { showRelated:false, minWeight:1 };

const svg = document.getElementById('svg');
const NS="http://www.w3.org/2000/svg";
let W, H;
const view = {x:0, y:0, k:1};
const gRoot = document.createElementNS(NS,'g'); svg.appendChild(gRoot);
const gLinks = document.createElementNS(NS,'g'); gRoot.appendChild(gLinks);
const gNodes = document.createElementNS(NS,'g'); gRoot.appendChild(gNodes);

function radius(n){ return 7 + Math.sqrt(n.count||1)*4; }
const EDGECOLOR = {"co-occurs":"#5b6a99","related":"#40507a","shared-tag":"#2f6d7a"};

let nodes=[], links=[], nodeEls=[], linkEls=[], idIndex={};
const posMemo = {};

// --- Project Navigation ---
function renderProjectList(){
  const list = document.getElementById('project-list');
  const slugs = Object.keys(ALL_PROJECTS);
  if(slugs.length === 0){
    list.innerHTML = '<div style="padding:16px;color:var(--muted);font-size:12px">No projects yet. Let an AI tool write some code with learnlance installed.</div>';
    return;
  }
  list.innerHTML = slugs.map(slug => {
    const p = ALL_PROJECTS[slug];
    const initial = (p.name||'?')[0].toUpperCase();
    const active = slug === CURRENT ? ' active' : '';
    return `<div class="proj-item${active}" data-slug="${slug}">
      <div class="proj-icon">${initial}</div>
      <div class="proj-info">
        <div class="proj-name">${esc(p.name)}</div>
        <div class="proj-meta">${p.concepts} concepts</div>
      </div>
    </div>`;
  }).join('');
  list.querySelectorAll('.proj-item').forEach(el => {
    el.addEventListener('click', () => switchProject(el.dataset.slug));
  });
  const total = slugs.reduce((s, k) => s + (ALL_PROJECTS[k].concepts||0), 0);
  document.getElementById('nav-footer').textContent = `${slugs.length} project${slugs.length>1?'s':''} · ${total} concepts total`;
}

function switchProject(slug){
  if(!GRAPHS[slug]) return;
  CURRENT = slug;
  DATA = GRAPHS[slug];
  rawNodes = DATA.nodes || {};
  rawEdges = DATA.edges || [];
  // Reset positions for new project
  Object.keys(posMemo).forEach(k => delete posMemo[k]);
  // Update header
  const p = ALL_PROJECTS[slug] || {};
  document.getElementById('proj-title').textContent = p.name || slug;
  document.getElementById('proj-subtitle').textContent = p.path || '';
  // Re-highlight nav
  document.querySelectorAll('.proj-item').forEach(el => {
    el.classList.toggle('active', el.dataset.slug === slug);
  });
  // Reset view
  W = svg.clientWidth; H = svg.clientHeight;
  view.x = W/2; view.y = H/2; view.k = 1;
  userMoved = false;
  rebuild();
  document.getElementById('detail').innerHTML = '<span class="empty">Click any concept to see what it is and where you met it.</span>';
}

// --- Graph Engine (same as before) ---
function buildData(){
  idIndex={};
  const visible = Object.values(rawNodes)
    .filter(n => filters.showRelated || !n.placeholder);
  nodes = visible.map(n => {
    const p = posMemo[n.id];
    return {...n,
      x: p ? p.x : (Math.random()-0.5)*600,
      y: p ? p.y : (Math.random()-0.5)*400, vx:0, vy:0};
  });
  nodes.forEach(n=>idIndex[n.id]=n);
  links = rawEdges
    .filter(e => (e.weight||1) >= filters.minWeight)
    .filter(e => idIndex[e.source] && idIndex[e.target])
    .map(e => ({source:idIndex[e.source], target:idIndex[e.target],
                type:e.type, weight:e.weight||1, tags:e.tags||[]}));
}

function buildEls(){
  gLinks.textContent=''; gNodes.textContent='';
  linkEls = links.map(l=>{
    const el=document.createElementNS(NS,'line');
    el.setAttribute('stroke', EDGECOLOR[l.type] || "#333a4d");
    el.setAttribute('stroke-width', (1 + Math.min(l.weight||1,6)*0.35).toFixed(2));
    el.setAttribute('stroke-opacity', l.type==='shared-tag' ? 0.55 : 0.8);
    const title=document.createElementNS(NS,'title');
    title.textContent = l.type + (l.tags&&l.tags.length?(' · '+l.tags.join(', ')):'') + ' x'+(l.weight||1);
    el.appendChild(title);
    gLinks.appendChild(el); return el;
  });
  nodeEls = nodes.map(n=>{
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
    g.addEventListener('mousedown',(e)=>{
      e.stopPropagation(); dragged=n; alpha=0.5;
      const w=toWorld(e.offsetX,e.offsetY); dragOff={x:w.x-n.x,y:w.y-n.y};
    });
    n._c=c; n._g=g;
    return g;
  });
}

function updateStats(){
  document.getElementById('s-topics').textContent =
    Object.values(rawNodes).filter(n=>!n.placeholder).length;
  document.getElementById('s-links').textContent = links.length;
  document.getElementById('s-turns').textContent = (DATA.meta&&DATA.meta.turns)||0;
  const cats = [...new Set(nodes.map(n=>n.category))];
  document.getElementById('legend').innerHTML = cats.map(c=>
    `<span><i class="dot" style="background:${color(c)}"></i>${c}</span>`).join('');
}

function rebuild(){
  for(const n of nodes){ posMemo[n.id] = {x:n.x, y:n.y}; }
  buildData(); buildEls(); updateStats();
  applySearch(); alpha=1;
}

// ---- force simulation ----
let alpha=1;
function tick(){
  alpha *= 0.985; if(alpha<0.02) alpha=0.02;
  for(let i=0;i<nodes.length;i++){
    for(let j=i+1;j<nodes.length;j++){
      const a=nodes[i], b=nodes[j];
      let dx=a.x-b.x, dy=a.y-b.y; let d2=dx*dx+dy*dy||0.01;
      const f = 2600/d2 * alpha;
      const d=Math.sqrt(d2); const fx=dx/d*f, fy=dy/d*f;
      a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy;
    }
  }
  for(const l of links){
    const a=l.source,b=l.target;
    let dx=b.x-a.x, dy=b.y-a.y; let d=Math.sqrt(dx*dx+dy*dy)||0.01;
    const target=90; const f=(d-target)*0.02*alpha;
    const fx=dx/d*f, fy=dy/d*f;
    a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy;
  }
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
  if(!userMoved){ view.x=W/2; view.y=H/2; }
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
    `<span class="tag">seen ${n.count||0}x</span>`+`<span class="tag">${conns} links</span>`+
    `<p>${esc(n.explanation)||'<span class="empty">A related concept — no explanation captured yet.</span>'}</p>`+
    (tags?`<div class="chips">${tags}</div>`:'')+
    ex;
  nodes.forEach(m=>m._c.setAttribute('stroke','#0b0d12'));
  n._c.setAttribute('stroke','#fff'); n._c.setAttribute('stroke-width',2.5);
}
function applySearch(){
  const q=(document.getElementById('q').value||'').toLowerCase();
  nodes.forEach(n=>{
    const hit=!q || n.name.toLowerCase().includes(q);
    n._g.style.opacity = hit?1:0.12;
  });
}
document.getElementById('q').addEventListener('input', applySearch);

// ---- controls ----
document.getElementById('showRelated').addEventListener('change',(e)=>{
  filters.showRelated = e.target.checked; rebuild();
});
document.getElementById('minW').addEventListener('input',(e)=>{
  filters.minWeight = +e.target.value;
  document.getElementById('minWv').textContent = e.target.value;
  rebuild();
});

// ---- init ----
function init(){
  W = svg.clientWidth; H = svg.clientHeight;
  view.x = W/2; view.y = H/2;
  renderProjectList();
  const slugs = Object.keys(ALL_PROJECTS);
  if(CURRENT && GRAPHS[CURRENT]){
    switchProject(CURRENT);
  } else if(slugs.length > 0){
    switchProject(slugs[0]);
  } else {
    DATA = {nodes:{},edges:[],meta:{turns:0}};
    rawNodes = {}; rawEdges = [];
    rebuild();
    document.getElementById('detail').innerHTML='<span class="empty">Nothing learned yet. Let an AI tool generate some code with learnlance installed, then refresh.</span>';
  }
  requestAnimationFrame(tick);
}
init();
</script>
</body>
</html>
"""


def _build_projects_data(current_slug: str | None = None) -> tuple[dict, dict, str | None]:
    """Build the ALL_PROJECTS and GRAPHS dicts for the HTML template.

    Returns (projects_meta, graphs, current_slug).
    """
    from . import graph

    registry = config.load_projects_registry()
    projects_meta = {}
    graphs = {}

    for slug, info in registry.items():
        cwd = info.get("path", "")
        gpath = config.project_graph_path(cwd)
        g = graph.load(gpath) if gpath.exists() else graph.empty()
        concepts = sum(1 for n in g.get("nodes", {}).values() if not n.get("placeholder"))
        projects_meta[slug] = {
            "name": info.get("name", slug),
            "path": cwd,
            "concepts": concepts,
            "turns": g.get("meta", {}).get("turns", 0),
        }
        graphs[slug] = g

    # If no current slug provided, pick the first one
    if current_slug is None and projects_meta:
        current_slug = next(iter(projects_meta))

    return projects_meta, graphs, current_slug


def render_html(graph_data: dict, path: Path | None = None) -> Path:
    """Render the full multi-project HTML. Legacy single-graph compatibility."""
    path = path or config.HTML_PATH
    config.ensure_home()

    projects_meta, graphs, current = _build_projects_data()

    # If there are no registered projects but we have graph data, show it as "default"
    if not projects_meta and graph_data.get("nodes"):
        projects_meta["default"] = {"name": "default", "path": "", "concepts": 0, "turns": 0}
        graphs["default"] = graph_data
        current = "default"

    html = _TEMPLATE.replace("__PROJECTS__", json.dumps(projects_meta))
    html = html.replace("__GRAPHS__", json.dumps(graphs))
    html = html.replace("__CURRENT__", json.dumps(current))
    path.write_text(html, encoding="utf-8")
    return path


def render_project_html(graph_data: dict, cwd: str) -> Path:
    """Render the multi-project HTML with a specific project selected.

    Also writes a per-project HTML file for direct access.
    """
    slug = config.register_project(cwd)
    config.ensure_home()

    # Write per-project HTML (single graph, no nav — for quick access)
    per_project_path = config.project_html_path(cwd)
    per_project_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the full multi-project HTML to the main location
    projects_meta, graphs, _ = _build_projects_data(current_slug=slug)

    # Make sure current project's graph is up to date
    graphs[slug] = graph_data
    concepts = sum(1 for n in graph_data.get("nodes", {}).values() if not n.get("placeholder"))
    if slug in projects_meta:
        projects_meta[slug]["concepts"] = concepts
        projects_meta[slug]["turns"] = graph_data.get("meta", {}).get("turns", 0)

    html = _TEMPLATE.replace("__PROJECTS__", json.dumps(projects_meta))
    html = html.replace("__GRAPHS__", json.dumps(graphs))
    html = html.replace("__CURRENT__", json.dumps(slug))

    # Write to main HTML path (the full dashboard)
    main_path = config.HTML_PATH
    main_path.write_text(html, encoding="utf-8")

    # Also write per-project copy
    per_project_path.write_text(html, encoding="utf-8")

    return main_path
