"""Interactive HTML visualization of the computation graph using D3.js."""

import json
import webbrowser
from pathlib import Path
from typing import Any

from ..types.graph import Node, ParameterRef, ParameterView, Result


def show_graph(
    node: Node[Any],
    filename: str = "graph.html",
    *,
    open_browser: bool = True,
) -> Path:
    """Render a computation graph as a self-contained interactive HTML page.

    Uses D3.js (loaded from CDN) to draw a force-directed graph.  Node types
    are visually differentiated:

    * **Result** nodes – rounded rectangles, blue palette
    * **ParameterView** nodes – circles, green palette
    * **ParameterRef** nodes – diamonds, amber palette
    * **Plain Node** values – rectangles, gray palette

    Clicking a node opens a detail panel showing its full value and any
    gradients attached to it.

    Args:
        node: Root node of the computation graph.
        filename: Destination HTML file path.
        open_browser: If *True*, open the file in the default browser.

    Returns:
        The resolved :class:`~pathlib.Path` of the written file.
    """
    nodes, links = _collect_graph(node)
    graph_json = json.dumps({"nodes": nodes, "links": links})

    html_content = _HTML_TEMPLATE.replace("__GRAPH_DATA__", graph_json)

    out = Path(filename).resolve()
    out.write_text(html_content, encoding="utf-8")

    if open_browser:
        webbrowser.open(out.as_uri())

    return out


# ── graph serialisation ──────────────────────────────────────────────


def _collect_graph(root: Node[Any]) -> tuple[list[dict], list[dict]]:
    visited: set[int] = set()
    nodes: list[dict] = []
    links: list[dict] = []
    id_map: dict[int, int] = {}  # python id → index in nodes list

    def _idx(obj: object) -> int:
        return id_map[id(obj)]

    def _add_ref(ref: ParameterRef) -> None:
        if id(ref) in visited:
            return
        visited.add(id(ref))
        id_map[id(ref)] = len(nodes)
        nodes.append(
            {
                "id": len(nodes),
                "name": f"ref:{ref.name}",
                "type": "ref",
                "value": ref.description or "",
                "gradients": [str(g) for g in ref.gradients],
            }
        )

    def _add_node(n: Node[Any]) -> None:
        if id(n) in visited:
            return
        visited.add(id(n))
        id_map[id(n)] = len(nodes)

        ntype = "node"
        if isinstance(n, Result):
            ntype = "result"
        elif isinstance(n, ParameterView):
            ntype = "param"

        nodes.append(
            {
                "id": len(nodes),
                "name": n.name,
                "type": ntype,
                "value": str(n.value),
                "gradients": [str(g) for g in n.gradients],
            }
        )

        if isinstance(n, Result):
            for inp in n.inputs:
                _add_node(inp)
                links.append({"source": _idx(inp), "target": _idx(n), "kind": "input"})
            for tr in n.tool_results:
                _add_node(tr)
                links.append({"source": _idx(tr), "target": _idx(n), "kind": "tool"})
        elif isinstance(n, ParameterView):
            _add_ref(n.source)
            links.append({"source": _idx(n.source), "target": _idx(n), "kind": "ref"})

    _add_node(root)
    return nodes, links


# ── HTML template ────────────────────────────────────────────────────

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Computation Graph</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
     background:#0f1117;color:#c9d1d9;overflow:hidden}
svg{display:block}
/* links */
.link{stroke-opacity:.5;fill:none}
.link.input{stroke:#58a6ff}
.link.tool{stroke:#f0883e;stroke-dasharray:6 3}
.link.ref{stroke:#d29922;stroke-dasharray:3 3}
/* node labels */
.node-label{font-size:11px;pointer-events:none;text-anchor:middle;fill:#c9d1d9;
             dominant-baseline:central;font-weight:500}
/* detail panel */
#detail{position:fixed;top:16px;right:16px;width:380px;max-height:calc(100vh - 32px);
        background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;
        overflow-y:auto;display:none;box-shadow:0 8px 24px rgba(0,0,0,.4)}
#detail h2{font-size:15px;margin-bottom:12px;color:#58a6ff;font-weight:600}
#detail .badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;
               font-weight:600;margin-bottom:12px}
#detail .badge.result{background:#1f3a5f;color:#79c0ff}
#detail .badge.param{background:#1a3a2a;color:#7ee787}
#detail .badge.ref{background:#3d2e00;color:#e3b341}
#detail .badge.node{background:#272b33;color:#8b949e}
#detail .section-title{font-size:12px;text-transform:uppercase;letter-spacing:.05em;
                       color:#8b949e;margin:14px 0 6px;font-weight:600}
#detail pre{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:12px;
            font-size:12px;white-space:pre-wrap;word-break:break-word;color:#c9d1d9;
            max-height:260px;overflow-y:auto;line-height:1.5}
#detail .grad-item{background:#0d1117;border:1px solid #21262d;border-radius:6px;
                   padding:10px;margin-bottom:6px;font-size:12px;white-space:pre-wrap;
                   word-break:break-word;line-height:1.5}
#detail .close-btn{position:absolute;top:12px;right:14px;background:none;border:none;
                   color:#8b949e;font-size:18px;cursor:pointer;line-height:1}
#detail .close-btn:hover{color:#c9d1d9}
.no-grad{color:#484f58;font-style:italic}
/* legend */
#legend{position:fixed;bottom:16px;left:16px;background:#161b22;border:1px solid #30363d;
        border-radius:8px;padding:12px 16px;font-size:12px;display:flex;gap:16px;
        align-items:center}
#legend .item{display:flex;align-items:center;gap:6px}
#legend .swatch{width:14px;height:14px;border-radius:3px}
</style>
</head>
<body>
<div id="detail">
  <button class="close-btn" onclick="document.getElementById('detail').style.display='none'">&times;</button>
  <div id="detail-content"></div>
</div>
<div id="legend">
  <div class="item"><div class="swatch" style="background:#1f6feb"></div>Result</div>
  <div class="item"><div class="swatch" style="background:#238636"></div>Parameter</div>
  <div class="item"><div class="swatch" style="background:#9e6a03"></div>Ref</div>
  <div class="item"><div class="swatch" style="background:#30363d;border:1px solid #484f58"></div>Value</div>
</div>
<div id="hint" style="position:fixed;top:16px;left:16px;background:#161b22;border:1px solid #30363d;
     border-radius:8px;padding:10px 16px;font-size:12px;color:#8b949e">
  Click on a node to view additional details such as its value and gradients.
</div>
<svg id="graph"></svg>
<script>
const data = __GRAPH_DATA__;
const width = window.innerWidth, height = window.innerHeight;
const nodeW=130, nodeH=40, nodeRx=10;

const svg = d3.select("#graph").attr("width", width).attr("height", height);
const g = svg.append("g");

// zoom
svg.call(d3.zoom().scaleExtent([.1, 4]).on("zoom", e => g.attr("transform", e.transform)));

// arrow markers
const defs = svg.append("defs");
["input","tool","ref"].forEach(kind => {
  const colors = {input:"#58a6ff",tool:"#f0883e",ref:"#d29922"};
  defs.append("marker").attr("id","arrow-"+kind).attr("viewBox","0 0 10 6")
    .attr("refX",10).attr("refY",3).attr("markerWidth",8).attr("markerHeight",6)
    .attr("orient","auto")
    .append("path").attr("d","M0,0 L10,3 L0,6").attr("fill",colors[kind]);
});

// ── compute depth (BFS from root, which is the first node) ──
// Links go source→target (child→parent), so root = nodes[0].
const depthMap = new Map();
const childrenOf = new Map(); // parent-id → [child-ids]
data.links.forEach(l => {
  const sid = typeof l.source === "object" ? l.source.id : l.source;
  const tid = typeof l.target === "object" ? l.target.id : l.target;
  if(!childrenOf.has(tid)) childrenOf.set(tid, []);
  childrenOf.get(tid).push(sid);
});
// BFS
{
  const q = [0];
  depthMap.set(0, 0);
  while(q.length){
    const cur = q.shift();
    const d = depthMap.get(cur);
    (childrenOf.get(cur)||[]).forEach(cid => {
      if(!depthMap.has(cid)){ depthMap.set(cid, d+1); q.push(cid); }
    });
  }
}
const maxDepth = Math.max(...depthMap.values(), 0);
data.nodes.forEach(n => { n.depth = depthMap.has(n.id) ? depthMap.get(n.id) : maxDepth; });

// ── horizontal ordering per layer (median heuristic to reduce crossings) ──
// Group nodes by depth layer
const layers = new Map(); // depth → [node]
data.nodes.forEach(n => {
  if(!layers.has(n.depth)) layers.set(n.depth, []);
  layers.get(n.depth).push(n);
});
// Build parent lookup: child-id → [parent-ids]
const parentsOf = new Map();
data.links.forEach(l => {
  const sid = typeof l.source === "object" ? l.source.id : l.source;
  const tid = typeof l.target === "object" ? l.target.id : l.target;
  if(!parentsOf.has(sid)) parentsOf.set(sid, []);
  parentsOf.get(sid).push(tid);
});
// Initial order: just the insertion order
const posInLayer = new Map(); // node-id → index within its layer
layers.forEach((arr) => arr.forEach((n,i) => posInLayer.set(n.id, i)));
// Sweep down then up a few times using median positions of connected nodes
for(let iter=0; iter<4; iter++){
  // top-down: for each layer (skip root), order by median position of parents
  const depths = [...layers.keys()].sort((a,b)=>a-b);
  for(let di=1; di<depths.length; di++){
    const arr = layers.get(depths[di]);
    arr.forEach(n => {
      const pids = parentsOf.get(n.id)||[];
      if(pids.length){
        const positions = pids.map(p=>posInLayer.get(p)).filter(p=>p!==undefined).sort((a,b)=>a-b);
        n._median = positions[Math.floor(positions.length/2)];
      } else { n._median = posInLayer.get(n.id); }
    });
    arr.sort((a,b)=>a._median-b._median);
    arr.forEach((n,i) => posInLayer.set(n.id, i));
  }
  // bottom-up: for each layer (skip deepest), order by median position of children
  for(let di=depths.length-2; di>=0; di--){
    const arr = layers.get(depths[di]);
    arr.forEach(n => {
      const cids = childrenOf.get(n.id)||[];
      if(cids.length){
        const positions = cids.map(c=>posInLayer.get(c)).filter(p=>p!==undefined).sort((a,b)=>a-b);
        n._median = positions[Math.floor(positions.length/2)];
      } else { n._median = posInLayer.get(n.id); }
    });
    arr.sort((a,b)=>a._median-b._median);
    arr.forEach((n,i) => posInLayer.set(n.id, i));
  }
}

// Assign initial positions based on layer ordering
const xSpacing = nodeW + 40;
const yMargin = 80;
function targetY(d){
  if(maxDepth===0) return height/2;
  return yMargin + (d.depth / maxDepth) * (height - 2*yMargin);
}
data.nodes.forEach(n => {
  const arr = layers.get(n.depth);
  const layerW = arr.length * xSpacing;
  const startX = width/2 - layerW/2 + xSpacing/2;
  n._targetX = startX + posInLayer.get(n.id) * xSpacing;
  n._targetY = targetY(n);
  n.x = n._targetX;
  n.y = n._targetY;
});

// simulation
const sim = d3.forceSimulation(data.nodes)
  .force("link", d3.forceLink(data.links).id(d=>d.id).distance(100).strength(0.3))
  .force("charge", d3.forceManyBody().strength(-300))
  .force("y", d3.forceY(d => d._targetY).strength(0.8))
  .force("x", d3.forceX(d => d._targetX).strength(0.3))
  .force("collide", d3.forceCollide(nodeW/2 + 8));

// links (using path so we can clip the arrow to the rect edge)
const link = g.selectAll(".link").data(data.links).join("line")
  .attr("class", d => "link " + d.kind)
  .attr("stroke-width", 1.5)
  .attr("marker-end", d => "url(#arrow-" + d.kind + ")");

// node groups
const nodeG = g.selectAll(".node-g").data(data.nodes).join("g")
  .attr("class","node-g").style("cursor","pointer")
  .call(d3.drag().on("start",dragStart).on("drag",dragged).on("end",dragEnd));

// shapes per type
nodeG.each(function(d){
  const el = d3.select(this);
  const cfg = {
    result:  {fill:"#1f6feb",stroke:"#58a6ff"},
    param:   {fill:"#238636",stroke:"#3fb950"},
    ref:     {fill:"#9e6a03",stroke:"#d29922"},
    node:    {fill:"#30363d",stroke:"#484f58"},
  }[d.type];
  el.append("rect").attr("width",nodeW).attr("height",nodeH)
    .attr("x",-nodeW/2).attr("y",-nodeH/2).attr("rx",nodeRx)
    .attr("fill",cfg.fill).attr("stroke",cfg.stroke).attr("stroke-width",1.5);
});

// labels
nodeG.append("text").attr("class","node-label")
  .text(d => d.name.length > 18 ? d.name.slice(0,16)+"…" : d.name);

// click → detail panel
nodeG.on("click", (ev, d) => {
  ev.stopPropagation();
  const typeLabel = {result:"Result",param:"ParameterView",ref:"ParameterRef",node:"Node"}[d.type];
  let h = `<span class="badge ${d.type}">${typeLabel}</span><h2>${esc(d.name)}</h2>`;
  h += `<div class="section-title">Value</div><pre>${esc(d.value||"(empty)")}</pre>`;
  if(d.gradients && d.gradients.length){
    h += `<div class="section-title">Gradients (${d.gradients.length})</div>`;
    d.gradients.forEach((g,i) => { h += `<div class="grad-item">${esc(g)}</div>`; });
  } else {
    h += `<div class="section-title">Gradients</div><p class="no-grad">No gradients recorded.</p>`;
  }
  document.getElementById("detail-content").innerHTML = h;
  document.getElementById("detail").style.display = "block";
});

svg.on("click", () => document.getElementById("detail").style.display = "none");

// ── clip line endpoint to rectangle border ──
function clipToRect(sx,sy,tx,ty){
  // Returns the point on the target's rect border closest to source.
  const hw=nodeW/2, hh=nodeH/2;
  const dx=sx-tx, dy=sy-ty;
  if(dx===0 && dy===0) return {x:tx,y:ty};
  const absDx=Math.abs(dx), absDy=Math.abs(dy);
  let scale;
  if(absDx/hw > absDy/hh){ scale=hw/absDx; } else { scale=hh/absDy; }
  return {x: tx + dx*scale, y: ty + dy*scale};
}

// tick
sim.on("tick", () => {
  link.each(function(d){
    const el = d3.select(this);
    // clip arrow to target rect edge
    const c = clipToRect(d.source.x, d.source.y, d.target.x, d.target.y);
    el.attr("x1",d.source.x).attr("y1",d.source.y).attr("x2",c.x).attr("y2",c.y);
  });
  nodeG.attr("transform", d => `translate(${d.x},${d.y})`);
});

// drag helpers
function dragStart(ev,d){if(!ev.active) sim.alphaTarget(.3).restart(); d.fx=d.x; d.fy=d.y;}
function dragged(ev,d){d.fx=ev.x; d.fy=ev.y;}
function dragEnd(ev,d){if(!ev.active) sim.alphaTarget(0); d.fx=null; d.fy=null;}

function esc(s){const t=document.createElement("span");t.textContent=s;return t.innerHTML;}
</script>
</body>
</html>"""
