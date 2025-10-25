
ChatGPT said:
Steps to Run (Debian) — Full Process

Log in to your Debian server via SSH.

Copy or clone the files into the ~/lxc_vps_bot/ directory
(you can either upload them manually or use git clone).

Make the run.sh script executable and run it:

`chmod +x run.sh`
`./run.sh`


→ This script will:

Install LXD (via Snap)

Run lxd init --auto

Create a Python virtual environment

Install all requirements

Create and start a systemd service

Edit the bot configuration:
Open ~/lxc_vps_bot/config.json and set your:

BOT_TOKEN

ADMIN_ROLE_ID

💡 To get the Role ID:
In Discord, right-click on the role → Copy ID (make sure Developer Mode is ON).

If you edit config.json after running run.sh, reload the service:

`sudo systemctl restart lxc-vps-bot.service`
`sudo journalctl -u lxc-vps-bot.service -f`


Enable Message Intents in Discord Developer Portal:

Go to your bot page → Bot tab

Under Privileged Gateway Intents, turn ON:
✅ Message Content Intent
