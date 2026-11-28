# Tic-Tac-Toe³

A phone-first tic-tac-toe arcade by **LJ** · Powered by SCODE · v3.1

**Play:** https://driftlinegame.github.io/tictactoe3/ (after GitHub Pages is turned on)

## Modes
- **Classic**: the original 3×3.
- **Ultimate**: 9 boards. The square you play sends your opponent to the matching board.
- **Cubed³**: 9 Ultimate games on one sheet (729 squares). Your board picks their game, your square picks their board. Each player gets 3 wormholes.

## Features
- Pass & Play on one phone, or vs AI: Easy, Medium, Hard, Extreme (perfect play in Classic; thousands of simulations per move in Ultimate & Cubed).
- Tap a square to preview the send line, then **Confirm** (or tap the square again).
- Cubed uses a live mini-map plus an auto-zoomed game you can actually tap.
- Original synthesized game-show sounds, haptics, confetti, and auto-save.
- One self-contained `index.html` (plus the optional tutorial video), no build step and no dependencies.

## Online play (v3.0)
The game server lives in [`server/`](server/) and runs on LJ's own computer through a Cloudflare tunnel. Setup steps: [`server/SETUP.md`](server/SETUP.md).
Free accounts with emailed codes, ranked matchmaking with Elo ratings and tiers, 4-digit friend games, daily / weekly / monthly / all-time leaderboards, and a local-only admin hub.

## Version history
- **v3.1**: new SCODE intro: tap ENTER on the splash to play the SCODE startup sound (an original swarm-into-chord sting) while the logo swells, then the game fades in. "Enter without sound" skips it.
- **v3.0**: Online mode. Accounts (sign up with a 6-digit email code, sign in, reset password, delete account), profiles with colour and avatar, ranked matchmaking per mode with Elo ratings, placement games and tiers (Bronze → Grandmaster), friend games with a 4-digit code, server-checked moves, turn timer, reconnecting, resign, rating change on the result screen, leaderboards (today, this week, this month, all-time), "servers offline" handling, plus the Python game server, admin hub and Windows launcher scripts.
- **v2.1**: fixed the PLAY button getting squished (text cut off) on iPhone.
- **v2.0**: big update. Built-in video tutorial (`tutorial.mp4`). Face-to-face layout for pass & play (each player gets their own Confirm button, map and status, O's is upside down at the top). Full effects system: send orb that flies to where your opponent must play, sparks and shockwaves on every move, "BOARD!" / "GAME WON!" stamps with screen shake, fireworks and confetti for wins, wormhole vortex, boards that pop in at the start, Cubed games that slide in from the direction they sit, AI "thinking" dots, turn-colour glow, and a grey-out for losses. Haptic bursts made much stronger.
- **v1.1**: vibration maxed out: stronger multi-tick haptic bursts (iPhone, iOS 18+) and full vibration patterns (Android), instant feedback on touch, and a "tap to test" vibration toggle on the home screen.
- **v1.0**: first release: Classic, Ultimate, Cubed³, pass & play, 4 AI levels, send preview + confirm, wormholes, sounds, auto-save.
