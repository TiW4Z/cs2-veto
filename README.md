# CS2 Map Veto — serverless, peer-to-peer

A single web page (`index.html`) that lets two teams do a CS2-style map veto in
real time. **No backend, no hosting cost, nothing you run.** The players'
browsers talk directly to each other over WebRTC.

**Live:** <https://tiw4z.github.io/cs2-veto/>

## Features

- **BO1** (Premier style — alternating bans down to one map) or **BO3**
  (ban, ban, pick, pick, ban…) — chosen when creating the lobby.
- **Whole teams can join, not just captains.** Each team has one invite link
  that everyone on that team opens; up to ~10 people total can be connected.
- **Team voting:** on a team's turn, every connected player on that team votes
  for the map, with a live tally and your own vote highlighted.
- **Host-configurable vote timer** (default 30s, 5–180s). When it runs out the
  leading option locks; ties are broken randomly. A multi-person team uses the
  full timer (so players can discuss/change votes); a solo/1v1 team locks as
  soon as everyone has voted.
- A 3-D **coin-flip animation** reveals who bans first when the host starts.
- Optional **side (CT/T) pick** after maps are locked — also a team vote.
- **Sound effects** (generated in-browser, no files): coin toss, last-5-seconds
  countdown ticks, ban/pick, and a final fanfare. A 🔊/🔇 mute toggle is in the
  header.
- **Live connected-player counts** per team (and total) are always shown.

## How it connects (and why there's no server)

The whole app is one static file. Whoever creates the lobby becomes the **host**:
their browser runs the veto and relays state to everyone else. Other players open
an invite link and their browsers connect **directly** to the host's browser over
WebRTC.

The only outside help is for the initial handshake:
- the free public **PeerJS broker** introduces the browsers to each other, and
- **Google's free STUN** server lets them discover their public IPs and punch
  through home routers (NAT) — no port forwarding needed.

After that, the veto data flows peer-to-peer. None of it touches a server you
run or pay for.

> Limitation: on a small number of very strict networks (some corporate / mobile
> carrier NATs) two browsers can't punch through without a paid relay (TURN),
> which this intentionally avoids. For a normal home/friend-group setup it works.

## Using it

1. Open the page and click **Create lobby**. Choose:
   - **Format** (BO1 / BO3) and **who bans first** (random / Team A / Team B),
   - **Team names**,
   - **Your role** — captain of Team A, captain of Team B, or just organizing
     (spectator),
   - the **map pool**, whether to add a **side pick**, and the **vote timer**.
2. You get **both invite links**: send your own team's link to your teammates
   ("Send to your teammates"), and the other link to the opposing team. Everyone
   on a team uses that team's link.
3. Keep the host tab open. When both teams show as connected, click
   **Start veto** → the coin flip reveals who bans first → each turn the active
   team votes, and the result locks when its vote resolves.
4. When the veto finishes, the **maps to play** (with picker and starting side)
   are listed for everyone.

> The host's browser is the authority and must stay open for the whole veto. If
> the host is also a captain, they vote directly in their own tab; their
> teammates join via the team link and vote alongside them.

## Hosting it yourself (one time, free)

Players need a URL to click, so the file must live at a public address. This repo
is already published with **GitHub Pages** (see the live link above). To host your
own copy, either:

- **GitHub Pages** — push `index.html` and the `maps/` folder to a repo and enable
  Pages (Settings → Pages → deploy from `main` / root). Permanent free link.
- **Netlify Drop** — go to <https://app.netlify.com/drop> and drag the project
  folder in. Instant free `https://…netlify.app` link.

Open *that* public URL to create lobbies, so the invite links use the public
address automatically.

## Local testing

Open `index.html` directly, create a lobby as **"just organizing"**, then open
the generated Team A and Team B links in two more tabs (or browsers) on the same
machine. They connect through the broker and you can drive both sides to see the
full flow.

## Files

- `index.html` — the entire app (deploy this plus the `maps/` folder).
- `maps/` — map thumbnail images used on the tiles and the create screen.
- `veto_server.py` — optional self-hosted LAN version; **not** needed for the
  peer-to-peer flow above.
