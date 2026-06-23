# CS2 Map Veto — serverless, peer-to-peer

A single web page (`index.html`) that lets two teams do a CS2-style map veto in
real time. **No backend, no hosting cost, nothing you run.** The two captains'
browsers talk directly to each other over WebRTC.

- **BO1** (Premier style — alternating bans to one map) or **BO3**
  (ban, ban, pick, pick, ban…) — chosen when creating the lobby.
- One **invite link per team** (or play as one team and send a single link).
  The same link can be shared with a **whole team — up to ~10 people total**
  can connect.
- **Team voting:** on a team's turn, every connected player votes for the map
  (live tally shown). A **30-second timer** runs; when it expires the leading
  map locks (ties broken randomly). A solo/1v1 team locks instantly.
- A 3-D **coin-flip animation** reveals who bans first when the host starts the veto.
- **Sound effects** (generated in-browser, no files): coin toss, last-5-seconds
  countdown ticks, ban/pick, and a final fanfare. A 🔊/🔇 mute toggle is in the header.
- **Live connected-player counts** per team (and total) are always shown.
- Optional **side (CT/T) pick** after maps are locked (also a team vote).

## How it connects (and why there's no server)

The whole app is one static file. When a captain creates a lobby, their browser
becomes the host. The other captain opens the invite link and their browser
connects **directly** to the host's browser using WebRTC.

The only outside help is for the initial handshake:
- the free public **PeerJS broker** introduces the two browsers, and
- **Google's free STUN** server lets them discover their public IPs and punch
  through home routers (NAT) — no port forwarding needed.

After that, the veto data flows peer-to-peer. None of it touches a server you
run or pay for.

> Limitation: on a small number of very strict networks (some corporate / mobile
> carrier NATs) two browsers can't punch through without a paid relay (TURN),
> which this intentionally avoids. For a normal home/friend-group setup it works.

## Using it

1. The host opens the page and clicks **Create lobby** (pick format, maps, who
   bans first, and your role).
2. They get an invite link per team. Send each team their link (Discord/DM/etc).
   Everyone on a team can open the same link.
3. Players open the link → connect automatically. When both teams show
   "connected", the host clicks **Start veto** → a coin flip reveals who bans
   first → teams vote each turn. The host keeps their tab open until it's done.

## Putting it online (one time, free)

Friends need a URL to click, so the file has to live at a public address. Pick
either — both are free and nothing "runs":

- **Netlify Drop** — go to <https://app.netlify.com/drop> and drag `index.html`
  in. You instantly get a public `https://…netlify.app` link. Easiest.
- **GitHub Pages** — push this folder to a GitHub repo and enable Pages in the
  repo settings. Permanent link under your account.

Open *that* public URL to create lobbies, so the invite links use the public
address automatically.

## Local testing

Open `index.html`, create a lobby as "just organizing", then open the generated
Team A and Team B links in two more tabs (or two browsers) on the same machine.
They'll connect through the broker and you can play both sides to see the flow.

## Files

- `index.html` — the entire app (this is the only file you deploy).
- `veto_server.py` — optional: a self-hosted LAN version (not needed for the
  peer-to-peer flow above).
