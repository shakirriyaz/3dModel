"""Self-contained HTML 3D viewer (no external dependencies) + scene JSON."""
from __future__ import annotations
import json
from ..model import Project, Route
from .. import rack as rackmod


def scene_dict(prj: Project, routes: dict[str, Route], stamp: str, qc=None, bom=None) -> dict:
    eq = []
    for e in prj.equipment:
        lo, hi = e.box()
        eq.append({"id": e.id, "kind": e.kind, "shape": e.shape, "lo": lo, "hi": hi, "area": e.area or "-",
                   "size": e.size, "elevation": e.elevation, "position": e.position,
                   "nozzles": [{"id": n.id, "p": e.nozzle_pos(n), "d": n.direction, "nps": n.nps} for n in e.nozzles]})
    lines = []
    for ln in prj.lines:
        rt = routes.get(ln.id)
        if rt and rt.status == "routed":
            dm = prj.dims(ln)
            lines.append({"id": ln.id, "nps": ln.nps, "r": dm.r, "od": dm.od, "takeoff": dm.takeoff, "points": rt.points,
                          "from": ln.from_, "to": ln.to, "spec": ln.spec, "service": ln.service,
                          "route": rt.note or "open routing",
                          "valves": [[v, p] for v, p in rt.valve_positions]})
    steel = [{"id": n, "lo": lo, "hi": hi} for n, lo, hi in rackmod.boxes(prj)]
    d = {"name": prj.name, "stamp": stamp, "equipment": eq, "lines": lines, "steel": steel,
         "failed": [{"id": k, "note": v.note} for k, v in routes.items() if v.status != "routed"]}
    if qc is not None:
        d["qc"] = json.loads(qc.model_dump_json())
    if bom is not None:
        d["bom"] = bom
    return d


def write_scene_json(prj, routes, path, stamp, qc=None, bom=None):
    with open(path, "w") as f:
        json.dump(scene_dict(prj, routes, stamp, qc, bom), f)


def write_viewer(prj, routes, path, stamp):
    data = json.dumps(scene_dict(prj, routes, stamp))
    with open(path, "w") as f:
        f.write(VIEWER.replace("__DATA__", data))


VIEWER = r"""<!doctype html><html><head><meta charset="utf-8"><title>Plant 3D viewer</title>
<style>html,body{margin:0;height:100%;background:#10151c;color:#dde;font:13px Arial}
canvas{display:block;width:100%;height:100%}#hud{position:fixed;left:10px;top:8px;pointer-events:none}
#leg{position:fixed;right:10px;top:8px;background:#0008;padding:6px 10px;border-radius:6px;max-height:80vh;overflow:auto}
.ok{color:#6d6}.bad{color:#f66}</style></head><body><canvas id="c"></canvas>
<div id="hud"></div><div id="leg"></div>
<script>
const D=__DATA__;
const cv=document.getElementById('c'),ctx=cv.getContext('2d');
let az=-0.9,el=0.55,zoom=1,pan=[0,0],drag=null;
const all=[...D.equipment.flatMap(e=>[e.lo,e.hi]),...D.lines.flatMap(l=>l.points),...(D.steel||[]).flatMap(e=>[e.lo,e.hi])];
const mn=[0,1,2].map(i=>Math.min(...all.map(p=>p[i]))),mx=[0,1,2].map(i=>Math.max(...all.map(p=>p[i])));
const ctr=mn.map((v,i)=>(v+mx[i])/2),span=Math.max(...mx.map((v,i)=>v-mn[i]));
const colors=['#4fc3f7','#ffb74d','#81c784','#ba68c8','#f06292','#fff176','#4db6ac','#a1887f'];
document.getElementById('hud').innerHTML='<b>'+D.name+'</b><br><span class="'+(D.stamp.includes('PASSED')?'ok':'bad')+'">'+D.stamp+'</span><br>drag: orbit · shift+drag: pan · wheel: zoom';
document.getElementById('leg').innerHTML=D.lines.map((l,i)=>'<div><span style="color:'+colors[i%8]+'">&#9632;</span> '+l.id+' ('+l.nps+'")</div>').join('');
function rot(p){let x=p[0]-ctr[0],y=p[1]-ctr[1],z=p[2]-ctr[2];
 let x1=x*Math.cos(az)-y*Math.sin(az),y1=x*Math.sin(az)+y*Math.cos(az);
 let y2=y1*Math.cos(el)-z*Math.sin(el),z2=y1*Math.sin(el)+z*Math.cos(el);return [x1,y2,z2];}
function pr(p){const q=rot(p),s=Math.min(cv.width,cv.height)/span*0.8*zoom;return [cv.width/2+pan[0]+q[0]*s,cv.height/2+pan[1]-q[2]*s,q[1],s];}
function line(a,b,col,w){const A=pr(a),B=pr(b);ctx.strokeStyle=col;ctx.lineWidth=w;ctx.beginPath();ctx.moveTo(A[0],A[1]);ctx.lineTo(B[0],B[1]);ctx.stroke();}
function box(lo,hi,col){const c=[];for(let i=0;i<8;i++)c.push([i&1?hi[0]:lo[0],i&2?hi[1]:lo[1],i&4?hi[2]:lo[2]]);
 [[0,1],[2,3],[4,5],[6,7],[0,2],[1,3],[4,6],[5,7],[0,4],[1,5],[2,6],[3,7]].forEach(e=>line(c[e[0]],c[e[1]],col,1));}
function cyl(e){const r=(e.hi[0]-e.lo[0])/2,cx=(e.lo[0]+e.hi[0])/2,cy=(e.lo[1]+e.hi[1])/2;
 for(const z of [e.lo[2],e.hi[2]]){let prev=null;for(let k=0;k<=24;k++){const a=k/24*Math.PI*2,p=[cx+r*Math.cos(a),cy+r*Math.sin(a),z];if(prev)line(prev,p,'#9ab',1);prev=p;}}
 for(let k=0;k<8;k++){const a=k/8*Math.PI*2;line([cx+r*Math.cos(a),cy+r*Math.sin(a),e.lo[2]],[cx+r*Math.cos(a),cy+r*Math.sin(a),e.hi[2]],'#9ab',1);}}
function cylh(e){const r=(e.hi[1]-e.lo[1])/2,cy=(e.lo[1]+e.hi[1])/2,cz=(e.lo[2]+e.hi[2])/2;
 for(const x of [e.lo[0],e.hi[0]]){let prev=null;for(let k=0;k<=24;k++){const a=k/24*Math.PI*2,p=[x,cy+r*Math.cos(a),cz+r*Math.sin(a)];if(prev)line(prev,p,'#9ab',1);prev=p;}}
 for(let k=0;k<8;k++){const a=k/8*Math.PI*2;line([e.lo[0],cy+r*Math.cos(a),cz+r*Math.sin(a)],[e.hi[0],cy+r*Math.cos(a),cz+r*Math.sin(a)],'#9ab',1);}}
function draw(){cv.width=innerWidth;cv.height=innerHeight;ctx.clearRect(0,0,cv.width,cv.height);
 // grid
 const g=Math.ceil(span/10/1000)*1000;for(let x=Math.floor(mn[0]/g)*g;x<=mx[0]+g;x+=g)line([x,mn[1]-g,0],[x,mx[1]+g,0],'#233',1);
 for(let y=Math.floor(mn[1]/g)*g;y<=mx[1]+g;y+=g)line([mn[0]-g,y,0],[mx[0]+g,y,0],'#233',1);
 D.equipment.forEach(e=>{if(e.shape==='cylinder_v')cyl(e);else if(e.shape==='cylinder_h')cylh(e);else box(e.lo,e.hi,'#9ab');
  const c=pr([(e.lo[0]+e.hi[0])/2,(e.lo[1]+e.hi[1])/2,e.hi[2]]);ctx.fillStyle='#cde';ctx.fillText(e.id,c[0]-12,c[1]-6);
  e.nozzles.forEach(n=>{const p=pr(n.p);ctx.fillStyle='#fff';ctx.fillRect(p[0]-2,p[1]-2,4,4);});});
 (D.steel||[]).forEach(e=>box(e.lo,e.hi,'#6b7a8f'));
 const segs=[];D.lines.forEach((l,i)=>{for(let k=0;k<l.points.length-1;k++){const a=l.points[k],b=l.points[k+1];segs.push({a,b,l,i,d:(pr(a)[2]+pr(b)[2])/2});}});
 segs.sort((p,q)=>q.d-p.d);
 segs.forEach(s=>{const sc=pr(s.a)[3];line(s.a,s.b,colors[s.i%8],Math.max(2,s.l.r*2*sc));});
 D.lines.forEach((l,i)=>{l.points.slice(1,-1).forEach(p=>{const q=pr(p);ctx.fillStyle=colors[i%8];ctx.beginPath();ctx.arc(q[0],q[1],Math.max(2,l.r*1.1*q[3]),0,7);ctx.fill();});
  l.valves.forEach(v=>{const q=pr(v[1]);ctx.fillStyle='#fc0';ctx.fillRect(q[0]-5,q[1]-5,10,10);});});}
cv.onmousedown=e=>drag={x:e.clientX,y:e.clientY,s:e.shiftKey};onmouseup=()=>drag=null;
onmousemove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;
 if(drag.s){pan[0]+=dx;pan[1]+=dy;}else{az+=dx*0.008;el=Math.max(-1.5,Math.min(1.5,el+dy*0.008));}draw();};
cv.onwheel=e=>{e.preventDefault();zoom*=e.deltaY<0?1.1:0.9;draw();};onresize=draw;draw();
</script></body></html>"""
