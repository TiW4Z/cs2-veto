"""
CS2 Map Veto — lobby server
===========================

One person (the host) opens the server, picks the map pool / format (BO1 or
BO3), and creates a lobby; a coin toss decides which team bans first. The
server hands back ONE
invite link per team (plus a spectator link). Each captain opens their link in
a browser and the two teams veto in real time, CS2-Premier style, with an
optional side (CT/T) pick once maps are locked.

Networking: a tiny self-hosted server + browser clients over WebSockets. This
is simpler and more reliable than true peer-to-peer (which needs a signalling
/ relay server for NAT traversal anyway). To play across the internet, run it
behind a free Cloudflare quick-tunnel:  python veto_server.py --tunnel
(requires `cloudflared` on PATH). Open the printed https URL and create the
lobby there so the invite links use the public address automatically.
"""

import sys
import json
import random
import secrets
import argparse
import subprocess
import threading

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect


# --------------------------------------------------------------------------
# Map pool
# --------------------------------------------------------------------------

MAP_POOL = {
    "de_ancient": "Ancient",
    "de_anubis": "Anubis",
    "de_dust2": "Dust II",
    "de_inferno": "Inferno",
    "de_mirage": "Mirage",
    "de_nuke": "Nuke",
    "de_train": "Train",
    "de_overpass": "Overpass",
    "de_vertigo": "Vertigo",
}
DEFAULT_MAPS = ["de_ancient", "de_anubis", "de_dust2", "de_inferno",
                "de_mirage", "de_nuke", "de_train"]


def display_name(m):
    return MAP_POOL.get(m, m)


def other(team):
    return "A" if team == "B" else "B"


# --------------------------------------------------------------------------
# Lobby model + veto state machine
# --------------------------------------------------------------------------

LOBBIES = {}  # id -> lobby dict


def build_map_steps(n, mode, first):
    """Ordered list of {team, action} for the map ban/pick phase."""
    if mode == "bo1":
        actions = ["ban"] * (n - 1)
    else:  # bo3
        actions = ["ban", "ban", "pick", "pick"]
        trailing = (n - 4) - 1
        if trailing > 0:
            actions += ["ban"] * trailing
    steps = []
    team = first
    for a in actions:
        steps.append({"team": team, "action": a})
        team = other(team)
    return steps


def create_lobby(mode, maps, names, first, side_pick):
    if first == "random":
        first = random.choice(["A", "B"])
    lid = secrets.token_urlsafe(5)
    lobby = {
        "id": lid,
        "mode": mode,
        "first": first,
        "side_pick": bool(side_pick),
        "team_names": {"A": names.get("A") or "Team A",
                       "B": names.get("B") or "Team B"},
        "tokens": {"A": secrets.token_urlsafe(8),
                   "B": secrets.token_urlsafe(8)},
        "maps": [{"name": m, "status": "available"} for m in maps],
        "steps": build_map_steps(len(maps), mode, first),
        "step_index": 0,
        "phase": "veto",
        "result": [],          # ordered played maps
        "side_steps": [],
        "side_index": 0,
        "log": [],
        "conns": set(),
    }
    LOBBIES[lid] = lobby
    return lobby


def _map(lobby, name):
    for m in lobby["maps"]:
        if m["name"] == name:
            return m
    return None


def apply_map_action(lobby, team, name):
    if lobby["phase"] != "veto":
        return "Veto phase is over."
    step = lobby["steps"][lobby["step_index"]]
    if step["team"] != team:
        return "It is not your turn."
    m = _map(lobby, name)
    if m is None or m["status"] != "available":
        return "That map is not available."

    action = step["action"]
    if action == "ban":
        m["status"] = "banned"
    else:  # pick
        m["status"] = "picked_" + team
        lobby["result"].append({"name": name, "picked_by": team,
                                "side": None, "side_by": None})
    lobby["log"].append({"team": team, "action": action, "map": name})
    lobby["step_index"] += 1

    if lobby["step_index"] >= len(lobby["steps"]):
        _finalize_map_phase(lobby)
    return None


def _finalize_map_phase(lobby):
    # whatever remains available becomes the decider / played map
    for m in lobby["maps"]:
        if m["status"] == "available":
            m["status"] = "decider"
            lobby["result"].append({"name": m["name"], "picked_by": None,
                                    "side": None, "side_by": None})

    if not lobby["side_pick"]:
        lobby["phase"] = "done"
        return

    # who picks side: opponent of the map's picker; for the decider, the
    # opponent of whoever made the last ban.
    last_ban_team = None
    for s in lobby["steps"]:
        if s["action"] == "ban":
            last_ban_team = s["team"]
    side_steps = []
    for r in lobby["result"]:
        picker = r["picked_by"]
        chooser = other(picker) if picker else other(last_ban_team or "A")
        side_steps.append({"team": chooser, "map": r["name"]})
    lobby["side_steps"] = side_steps
    lobby["side_index"] = 0
    lobby["phase"] = "side"


def apply_side(lobby, team, side):
    if lobby["phase"] != "side":
        return "Not in the side-pick phase."
    step = lobby["side_steps"][lobby["side_index"]]
    if step["team"] != team:
        return "It is not your turn to pick a side."
    if side not in ("CT", "T"):
        return "Invalid side."
    for r in lobby["result"]:
        if r["name"] == step["map"]:
            r["side"] = side
            r["side_by"] = team
    lobby["log"].append({"team": team, "action": "side", "map": step["map"],
                         "side": side})
    lobby["side_index"] += 1
    if lobby["side_index"] >= len(lobby["side_steps"]):
        lobby["phase"] = "done"
    return None


def current_action(lobby):
    if lobby["phase"] == "veto":
        s = lobby["steps"][lobby["step_index"]]
        return {"team": s["team"], "action": s["action"], "map": None}
    if lobby["phase"] == "side":
        s = lobby["side_steps"][lobby["side_index"]]
        return {"team": s["team"], "action": "side", "map": s["map"]}
    return None


def state_for(lobby):
    return {
        "id": lobby["id"],
        "mode": lobby["mode"],
        "phase": lobby["phase"],
        "first": lobby["first"],
        "side_pick": lobby["side_pick"],
        "team_names": lobby["team_names"],
        "maps": [{"name": m["name"], "display": display_name(m["name"]),
                  "status": m["status"]} for m in lobby["maps"]],
        "current": current_action(lobby),
        "result": [{"name": r["name"], "display": display_name(r["name"]),
                    "picked_by": r["picked_by"], "side": r["side"],
                    "side_by": r["side_by"]} for r in lobby["result"]],
        "log": [{"team": l["team"], "action": l["action"],
                 "map": l["map"], "display": display_name(l["map"]),
                 "side": l.get("side")} for l in lobby["log"]],
    }


async def broadcast(lobby):
    msg = json.dumps({"type": "state", "state": state_for(lobby)})
    dead = []
    for ws in list(lobby["conns"]):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        lobby["conns"].discard(ws)


# --------------------------------------------------------------------------
# HTTP + WebSocket endpoints
# --------------------------------------------------------------------------

async def home(request):
    return HTMLResponse(CREATE_HTML)


async def api_create(request):
    data = await request.json()
    mode = data.get("mode", "bo1")
    maps = [m for m in data.get("maps", []) if m in MAP_POOL]
    names = data.get("names", {})
    first = data.get("first", "random")
    side_pick = data.get("side_pick", True)

    n = len(maps)
    if mode == "bo1" and n < 2:
        return JSONResponse({"error": "Pick at least 2 maps for BO1."}, 400)
    if mode == "bo3" and n < 5:
        return JSONResponse({"error": "Pick at least 5 maps for BO3."}, 400)

    lobby = create_lobby(mode, maps, names, first, side_pick)
    lid = lobby["id"]
    return JSONResponse({
        "lobby": lid,
        "links": {
            "A": f"/lobby/{lid}?team=A&token={lobby['tokens']['A']}",
            "B": f"/lobby/{lid}?team=B&token={lobby['tokens']['B']}",
            "spectator": f"/lobby/{lid}?team=spec",
        },
        "team_names": lobby["team_names"],
        "first": lobby["first"],
    })


async def lobby_page(request):
    lid = request.path_params["id"]
    if lid not in LOBBIES:
        return HTMLResponse("<h2>Lobby not found.</h2>", 404)
    return HTMLResponse(LOBBY_HTML)


async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    lid = ws.query_params.get("lobby")
    team = ws.query_params.get("team", "spec")
    token = ws.query_params.get("token", "")
    lobby = LOBBIES.get(lid)
    if lobby is None:
        await ws.send_text(json.dumps({"type": "error",
                                       "message": "Lobby not found."}))
        await ws.close()
        return
    if team in ("A", "B") and token != lobby["tokens"][team]:
        await ws.send_text(json.dumps({"type": "error",
                                       "message": "Invalid invite token."}))
        await ws.close()
        return

    lobby["conns"].add(ws)
    await ws.send_text(json.dumps({"type": "state", "state": state_for(lobby),
                                   "you": team}))
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            if team not in ("A", "B"):
                continue  # spectators can't act
            err = None
            if data.get("type") == "pick":
                err = apply_map_action(lobby, team, data.get("map"))
            elif data.get("type") == "side":
                err = apply_side(lobby, team, data.get("side"))
            if err:
                await ws.send_text(json.dumps({"type": "error", "message": err}))
            else:
                await broadcast(lobby)
    except WebSocketDisconnect:
        pass
    finally:
        lobby["conns"].discard(ws)


routes = [
    Route("/", home),
    Route("/api/create", api_create, methods=["POST"]),
    Route("/lobby/{id}", lobby_page),
    WebSocketRoute("/ws", ws_endpoint),
]
app = Starlette(routes=routes)


# --------------------------------------------------------------------------
# Embedded front-end
# --------------------------------------------------------------------------

CREATE_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CS2 Map Veto — Create lobby</title>
<style>
:root{color-scheme:dark}
body{font-family:system-ui,Segoe UI,Arial;background:#15171c;color:#e7e9ee;
 max-width:720px;margin:0 auto;padding:24px}
h1{font-size:22px} h2{font-size:16px;margin-top:24px;color:#9fb4ff}
label{display:block;margin:6px 0}
.maps{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.maps label{background:#1f232b;border:1px solid #2c313c;border-radius:8px;
 padding:8px 10px;cursor:pointer}
input[type=text]{background:#1f232b;border:1px solid #2c313c;color:#e7e9ee;
 border-radius:6px;padding:6px 8px;width:200px}
button{background:#3b5bdb;color:#fff;border:0;border-radius:8px;padding:10px 16px;
 font-size:15px;cursor:pointer;margin-top:14px}
.row{display:flex;gap:24px;flex-wrap:wrap}
.links a{color:#8ce99a;word-break:break-all}
.linkbox{background:#1f232b;border:1px solid #2c313c;border-radius:8px;
 padding:10px;margin:8px 0}
.copy{background:#2c313c;padding:4px 10px;font-size:12px;margin:0 0 0 8px}
.err{color:#ff8787}
</style></head><body>
<h1>CS2 Map Veto — create a lobby</h1>
<h2>Format</h2>
<label><input type="radio" name="mode" value="bo1" checked> Best of 1 (Premier style — alternating bans)</label>
<label><input type="radio" name="mode" value="bo3"> Best of 3 (ban, ban, pick, pick, ban…)</label>
<h2>Team names</h2>
<div class="row">
 <input type="text" id="nameA" placeholder="Team A">
 <input type="text" id="nameB" placeholder="Team B">
</div>
<h2>Map pool</h2>
<div class="maps" id="maps"></div>
<button id="create">Create lobby & get invite links</button>
<div class="err" id="err"></div>
<div id="out"></div>
<script>
const POOL = __POOL__;
const DEFAULT = __DEFAULT__;
const mapsDiv = document.getElementById('maps');
for(const [k,v] of Object.entries(POOL)){
  const checked = DEFAULT.includes(k) ? 'checked':'';
  mapsDiv.insertAdjacentHTML('beforeend',
   `<label><input type="checkbox" value="${k}" ${checked}> ${v}</label>`);
}
function link(label, path){
  const url = location.origin + path;
  return `<div class="linkbox"><b>${label}</b><br><a href="${url}">${url}</a>
   <button class="copy" onclick="navigator.clipboard.writeText('${url}')">copy</button></div>`;
}
document.getElementById('create').onclick = async () => {
  document.getElementById('err').textContent='';
  const mode = document.querySelector('input[name=mode]:checked').value;
  const first = "random";   // first ban is always decided by the coin toss
  const maps = [...document.querySelectorAll('#maps input:checked')].map(c=>c.value);
  const body = {mode, first, maps,
    names:{A:nameA.value, B:nameB.value}, side_pick:true};
  const r = await fetch('/api/create',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d = await r.json();
  if(!r.ok){ document.getElementById('err').textContent = d.error; return; }
  document.getElementById('out').innerHTML =
    `<h2>Lobby created — first ban: Team ${d.first}</h2><div class="links">`+
    link('Send to '+d.team_names.A+' (Team A)', d.links.A)+
    link('Send to '+d.team_names.B+' (Team B)', d.links.B)+
    link('Spectator / your view', d.links.spectator)+`</div>`;
};
</script></body></html>"""

LOBBY_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CS2 Map Veto</title>
<style>
:root{color-scheme:dark}
body{font-family:system-ui,Segoe UI,Arial;background:#15171c;color:#e7e9ee;
 max-width:860px;margin:0 auto;padding:20px}
.bar{display:flex;justify-content:space-between;align-items:center}
.teamtag{font-weight:700;padding:4px 10px;border-radius:6px}
.turn{background:#2b3a67;border:1px solid #3b5bdb;border-radius:10px;
 padding:10px 14px;margin:14px 0;font-size:17px}
.maps{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
 gap:10px;margin-top:8px}
.map{background:#1f232b;border:1px solid #2c313c;border-radius:10px;
 padding:18px 12px;text-align:center;font-size:16px;position:relative;
 transition:.1s}
.map.available.mine{cursor:pointer;border-color:#3b5bdb}
.map.available.mine:hover{background:#26304a}
.map.banned{opacity:.35;text-decoration:line-through}
.map.picked_A{border-color:#8ce99a;background:#1c2a20}
.map.picked_B{border-color:#ffd43b;background:#2a261c}
.map.decider{border-color:#9fb4ff;background:#1e2440}
.tag{position:absolute;top:6px;right:8px;font-size:11px;opacity:.8}
.side{margin-top:6px}
.side button{background:#3b5bdb;color:#fff;border:0;border-radius:6px;
 padding:6px 12px;margin:4px 3px;cursor:pointer}
.result{background:#1f232b;border:1px solid #2c313c;border-radius:10px;
 padding:14px;margin-top:18px}
.log{font-size:13px;color:#9aa3b2;margin-top:14px;max-height:160px;overflow:auto}
.err{color:#ff8787;min-height:18px}
</style></head><body>
<div class="bar">
 <h2 id="title">CS2 Map Veto</h2>
 <div id="me"></div>
</div>
<div class="turn" id="turn">Connecting…</div>
<div class="err" id="err"></div>
<div class="maps" id="maps"></div>
<div id="result"></div>
<div class="log" id="log"></div>
<script>
const parts = location.pathname.split('/');
const lobby = parts[parts.length-1];
const q = new URLSearchParams(location.search);
const team = q.get('team') || 'spec';
const token = q.get('token') || '';
let ws, S=null;

function connect(){
  const proto = location.protocol==='https:'?'wss':'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws?lobby=${lobby}&team=${team}&token=${encodeURIComponent(token)}`);
  ws.onmessage = e => {
    const d = JSON.parse(e.data);
    if(d.type==='error'){ document.getElementById('err').textContent = d.message; return; }
    if(d.type==='state'){ S = d.state; render(); }
  };
  ws.onclose = () => setTimeout(connect, 1500);
}
function send(o){ document.getElementById('err').textContent=''; ws.send(JSON.stringify(o)); }

function render(){
  const tn = S.team_names;
  document.getElementById('title').textContent =
    `${tn.A}  vs  ${tn.B}  ·  ${S.mode.toUpperCase()}`;
  document.getElementById('me').innerHTML = team==='spec' ? '<i>spectating</i>'
    : `You are <span class="teamtag" style="background:${team==='A'?'#1c2a20':'#2a261c'}">${tn[team]}</span>`;

  // turn banner
  const t = document.getElementById('turn');
  if(S.phase==='done'){ t.textContent = '✅ Veto complete'; }
  else if(S.current){
    const cur = S.current; const who = tn[cur.team];
    const myturn = cur.team===team;
    let verb = cur.action==='ban'?'ban a map':cur.action==='pick'?'pick a map':
               `pick a side on ${displayOf(cur.map)}`;
    t.textContent = (myturn?'➡️ Your turn — ':`Waiting for ${who} to `) + verb;
  }

  // maps grid
  const mine = S.current && S.current.team===team;
  const md = document.getElementById('maps'); md.innerHTML='';
  for(const m of S.maps){
    const clickable = mine && S.phase==='veto' && m.status==='available';
    const tag = m.status==='banned'?'banned'
      :m.status==='picked_A'?'pick '+tn.A:m.status==='picked_B'?'pick '+tn.B
      :m.status==='decider'?'decider':'';
    const el = document.createElement('div');
    el.className = `map ${m.status} ${clickable?'mine':''}`;
    el.innerHTML = `${m.display}<span class="tag">${tag}</span>`;
    if(clickable) el.onclick = ()=>send({type:'pick', map:m.name});
    md.appendChild(el);
  }

  // side pick controls
  if(S.phase==='side' && S.current && S.current.team===team){
    const r = document.getElementById('result');
    r.innerHTML = `<div class="result"><b>Pick your starting side on
      ${displayOf(S.current.map)}</b><div class="side">
      <button onclick="send({type:'side',side:'CT'})">CT</button>
      <button onclick="send({type:'side',side:'T'})">T</button></div></div>`;
  } else {
    renderResult();
  }
  renderLog();
}
function displayOf(name){ const m=S.maps.find(x=>x.name===name); return m?m.display:name; }
function renderResult(){
  const r = document.getElementById('result');
  if(!S.result.length){ r.innerHTML=''; return; }
  let h = '<div class="result"><b>Maps to play</b><ol>';
  for(const m of S.result){
    let extra = m.picked_by?` (pick — ${S.team_names[m.picked_by]})`:' (decider)';
    let side = m.side?` · ${S.team_names[m.side_by]} starts ${m.side}`:'';
    h += `<li>${m.display}${extra}${side}</li>`;
  }
  r.innerHTML = h+'</ol></div>';
}
function renderLog(){
  const l = document.getElementById('log');
  l.innerHTML = S.log.map(e=>{
    const who = S.team_names[e.team];
    if(e.action==='side') return `${who} picked ${e.side} on ${e.display}`;
    return `${who} ${e.action==='ban'?'banned':'picked'} ${e.display}`;
  }).map(s=>'• '+s).join('<br>');
}
connect();
</script></body></html>"""

CREATE_HTML = (CREATE_HTML
               .replace("__POOL__", json.dumps(MAP_POOL))
               .replace("__DEFAULT__", json.dumps(DEFAULT_MAPS)))


# --------------------------------------------------------------------------
# Optional Cloudflare quick-tunnel for internet play
# --------------------------------------------------------------------------

def start_tunnel(port):
    try:
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1)
    except FileNotFoundError:
        print("\n[tunnel] 'cloudflared' not found on PATH.")
        print("[tunnel] Install it (winget install Cloudflare.cloudflared)"
              " or run without --tunnel on a LAN.\n")
        return

    def reader():
        for line in proc.stdout:
            if "trycloudflare.com" in line:
                url = line[line.find("https://"):].strip().split()[0]
                print("\n" + "=" * 60)
                print(f"  PUBLIC URL:  {url}")
                print("  Open that URL, create the lobby, and the invite")
                print("  links will use this public address automatically.")
                print("=" * 60 + "\n")
    threading.Thread(target=reader, daemon=True).start()


def main():
    ap = argparse.ArgumentParser(description="CS2 Map Veto lobby server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--tunnel", action="store_true",
                    help="Expose a public URL via cloudflared (internet play)")
    args = ap.parse_args()

    print(f"\nCS2 Map Veto server running.")
    print(f"  Local:   http://localhost:{args.port}")
    print(f"  On LAN:  http://<your-LAN-ip>:{args.port}")
    if args.tunnel:
        start_tunnel(args.port)
    else:
        print("  Internet play: re-run with  --tunnel  for a public URL.\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
