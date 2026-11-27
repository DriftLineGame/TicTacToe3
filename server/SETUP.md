# Tic-Tac-Toe³ game server: setup (Windows)

Everything runs from the `server` folder. Nothing secret ever goes on GitHub: `email-password.txt`, `admin-password.txt`,
`secret.key`, `config.json` and the database are all in `.gitignore`.

## 1. Install (once)
1. **Git for Windows**: https://git-scm.com/download/win (all the default options are fine).
2. Open **Command Prompt** and download the project:
   ```
   cd %USERPROFILE%
   git clone https://github.com/DriftLineGame/tictactoe3.git
   ```
   You now have `C:\Users\<you>\tictactoe3\server`.
3. Double-click **`server\setup.bat`**. It installs the one Python package the server needs (aiohttp) and Cloudflare's tunnel (cloudflared).

## 2. Email codes
1. Sign in to Google as **scodeai2026@gmail.com** → Google Account → Security → 2-Step Verification → **App passwords**.
2. Make one called `Tic-Tac-Toe3`. Copy the 16 letters.
3. In the `server` folder make a text file **`email-password.txt`** and paste the 16 letters in it. Save.

No file = the server still works, but codes are printed in the server window instead of emailed (handy for testing).

## 3. Start it
Double-click **`start-server.bat`**. The window shows:
- the **admin password** (first run only; it's also saved in `admin-password.txt`)
- a big **GAME SERVER LINK** (`https://something.trycloudflare.com`)
- then it saves `server.json` and pushes it to GitHub, so the website finds the server about a minute later.

The first push opens a GitHub sign-in window. Sign in once and it remembers you.
If the push ever fails, update it by hand: on github.com open the repo → `server.json` → ✏️ edit → paste
`{"server": "https://the-link.trycloudflare.com"}` → Commit. (The link is already copied to your clipboard.)

Check: open the game website → **Online**. You should see a green dot: "Game servers online".

## 4. Admin page
Double-click **`open-admin.bat`** (or open http://127.0.0.1:5050). It only works on this computer, never over the internet.
Password: in `admin-password.txt`.

## 5. Start by itself when the computer turns on
Double-click **`install-autostart.bat`**. The server now starts every time you sign in to Windows (`remove-autostart.bat` undoes it).
If you want it to start with no one signing in, turn on automatic sign-in: press Win+R, type `netplwiz`, untick
"Users must enter a user name and password", and enter your password.

## Day to day
- Turning the computer off (like 11 PM to 7 AM) is fine. The website shows "Game servers are offline" and people can still play Pass & Play and vs AI.
- Each start makes a **new** tunnel link and pushes it automatically.
- To get game updates: each time the server pushes `server.json` it also pulls the newest code. Restart the server to run it.
- No port forwarding is needed. If Windows Firewall asks about Python, you can click **Cancel**; the tunnel works without it.
- Settings (turn timer, ports, email limit) are in `config.json`, which is created on first run.
