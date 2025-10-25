#!/usr/bin/env python3
"""
lxc_vps_bot.py
Production-ready Discord bot to manage LXC VPS (Debian + LXD).

Place config.json + plans.json beside this script.
Run under Python venv. On Debian you can use run.sh to bootstrap & create systemd service.
"""

import asyncio
import json
import os
import re
import shlex
import sqlite3
from datetime import datetime
from typing import Optional, Tuple

import discord
from discord.ext import commands

# ---------- Paths ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
PLANS_PATH = os.path.join(BASE_DIR, "plans.json")
DB_PATH = os.path.join(BASE_DIR, "bot_data.db")

# ---------- Load config ----------
if not os.path.exists(CONFIG_PATH):
    raise SystemExit(f"Missing config.json at {CONFIG_PATH}. Create it and add BOT_TOKEN + ADMIN_ROLE_ID.")

with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

TOKEN = CONFIG.get("BOT_TOKEN")
ADMIN_ROLE_ID = str(CONFIG.get("ADMIN_ROLE_ID"))
LXC_PATH = CONFIG.get("LXC_PATH", "/usr/bin/lxc")
FAKE_IF_NO_LXC = bool(CONFIG.get("FAKE_MODE_IF_NO_LXC", False))

if not TOKEN:
    raise SystemExit("BOT_TOKEN missing in config.json")

# ---------- Load plans ----------
if not os.path.exists(PLANS_PATH):
    raise SystemExit("Missing plans.json - create it (run.sh creates default)")

with open(PLANS_PATH, "r") as f:
    PLANS = json.load(f)

# ---------- Helpers ----------
NAME_RE = re.compile(r"[^a-z0-9-]")

def save_plans():
    with open(PLANS_PATH, "w") as f:
        json.dump(PLANS, f, indent=2)
    return True

# ---------- DB ----------
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    credits INTEGER DEFAULT 0
)""")
c.execute("""CREATE TABLE IF NOT EXISTS vps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    container_name TEXT,
    plan TEXT,
    ram_mb INTEGER,
    cpu_cores INTEGER,
    arch TEXT,
    status TEXT,
    created_at TEXT
)""")
conn.commit()

def get_credits(user_id:int)->int:
    cur = conn.cursor()
    cur.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    return r[0] if r else 0

def add_credits(user_id:int, amount:int):
    cur = conn.cursor()
    cur.execute("INSERT INTO users (user_id, credits) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET credits = credits + ?", (user_id, amount, amount))
    conn.commit()

def remove_credits(user_id:int, amount:int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
    r = cur.fetchone()
    if not r: return False
    new = max(0, r[0] - amount)
    cur.execute("UPDATE users SET credits = ? WHERE user_id = ?", (new, user_id))
    conn.commit()
    return True

def create_vps_record(user_id:int, container_name:str, plan:str, ram:int, cpu:int, arch:str):
    cur = conn.cursor()
    cur.execute("INSERT INTO vps (user_id, container_name, plan, ram_mb, cpu_cores, arch, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (user_id, container_name, plan, ram, cpu, arch, "running", datetime.utcnow().isoformat()))
    conn.commit()
    return cur.lastrowid

def get_user_vps(user_id:int):
    cur = conn.cursor()
    cur.execute("SELECT * FROM vps WHERE user_id = ?", (user_id,))
    return cur.fetchall()

def get_vps_by_id(vps_id:int):
    cur = conn.cursor()
    cur.execute("SELECT * FROM vps WHERE id = ?", (vps_id,))
    return cur.fetchone()

def delete_vps_record(vps_id:int):
    cur = conn.cursor()
    cur.execute("DELETE FROM vps WHERE id = ?", (vps_id,))
    conn.commit()

# ---------- LXC helpers (async) ----------
async def run_lxc_cmd(args:list[str], timeout:int=300) -> Tuple[int,str,str]:
    """Run lxc command (list of args). Returns (code, stdout, stderr)."""
    # Fake mode if LXC not present
    if (not os.path.exists(LXC_PATH) and FAKE_IF_NO_LXC) or ("microsoft" in open("/proc/version").read().lower()):
        await asyncio.sleep(0.2)
        cmd_str = " ".join(shlex.quote(a) for a in args)
        return 0, f"(fake) {cmd_str}", ""
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")

async def create_container(name:str, image:str=None, profiles:str=None, ram_mb:Optional[int]=None, cpu_cores:Optional[int]=None, network:Optional[str]=None) -> Tuple[bool,str]:
    image = image or "images:ubuntu/22.04"
    profiles = profiles or "default"
    rc, out, err = await run_lxc_cmd([LXC_PATH, "launch", image, name, "-p", profiles])
    if rc != 0:
        return False, err or out
    if ram_mb is not None:
        mem_bytes = ram_mb * 1024 * 1024
        await run_lxc_cmd([LXC_PATH, "config", "set", name, "limits.memory", str(mem_bytes)])
    if cpu_cores is not None:
        await run_lxc_cmd([LXC_PATH, "config", "set", name, "limits.cpu", str(cpu_cores)])
    return True, out or "created"

async def delete_container(name:str) -> Tuple[bool,str]:
    await run_lxc_cmd([LXC_PATH, "stop", name, "--force"])
    rc, out, err = await run_lxc_cmd([LXC_PATH, "delete", name])
    if rc != 0:
        return False, err or out
    return True, "deleted"

async def action_container(name:str, action:str) -> Tuple[bool,str]:
    if action not in ("start","stop","restart","info"):
        return False, "invalid action"
    if action == "info":
        rc,out,err = await run_lxc_cmd([LXC_PATH, "info", name])
    else:
        rc,out,err = await run_lxc_cmd([LXC_PATH, action, name])
    if rc != 0:
        return False, err or out
    return True, out or "OK"

# ---------- Discord bot ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

def is_admin_ctx(ctx):
    if ctx.author.guild_permissions.administrator:
        return True
    try:
        rid = int(ADMIN_ROLE_ID)
        return any(role.id == rid for role in ctx.author.roles)
    except Exception:
        return False

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} | LXC path: {LXC_PATH} | FAKE_IF_NO_LXC={FAKE_IF_NO_LXC}")

# ---------- Commands (user) ----------
@bot.command(name="plans")
async def cmd_plans(ctx):
    embed = discord.Embed(title="Available VPS Plans", color=discord.Color.green())
    for key, p in PLANS.items():
        embed.add_field(name=f"{key} — {p.get('name','')}", value=f"{p['ram_mb']}MB RAM • {p['cpu']} CPU • {p['disk_gb']}GB disk • {p['price']} credits", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="buyc")
async def cmd_buyc(ctx):
    await ctx.send("To buy credits contact an admin. (Payment integration not included in this demo.)")

@bot.command(name="credits")
async def cmd_credits(ctx):
    amt = get_credits(ctx.author.id)
    await ctx.send(f"{ctx.author.mention} — you have **{amt}** credits.")

@bot.command(name="buywc")
async def cmd_buywc(ctx, plan:str, arch:str="intel"):
    plan = plan.lower()
    arch = arch.lower()
    if plan not in PLANS:
        return await ctx.send("Unknown plan. Use `!plans` to see available plans.")
    cost = PLANS[plan]["price"]
    user_credits = get_credits(ctx.author.id)
    if user_credits < cost:
        return await ctx.send(f"You need {cost} credits but have {user_credits}.")
    base = f"user{ctx.author.id}-{plan}"
    safe = NAME_RE.sub("", base.lower())
    suffix = datetime.utcnow().strftime("%y%m%d%H%M%S")
    container_name = f"{safe}-{suffix}"
    await ctx.send(f"Creating container `{container_name}` (plan {plan}) — this may take a minute...")
    ok,msg = await create_container(container_name, image="images:ubuntu/22.04", profiles="default", ram_mb=PLANS[plan]["ram_mb"], cpu_cores=PLANS[plan]["cpu"])
    if not ok:
        return await ctx.send(f"Failed to create container: ```{msg}```")
    remove_credits(ctx.author.id, cost)
    vps_id = create_vps_record(ctx.author.id, container_name, plan, PLANS[plan]["ram_mb"], PLANS[plan]["cpu"], arch)
    await ctx.send(f"✅ Container `{container_name}` created (ID {vps_id}). {cost} credits deducted.")

@bot.command(name="myvps")
async def cmd_myvps(ctx):
    rows = get_user_vps(ctx.author.id)
    if not rows:
        return await ctx.send("You have no VPS.")
    lines=[]
    for r in rows:
        lines.append(f"ID {r['id']}: `{r['container_name']}` — {r['plan']} — {r['ram_mb']}MB/{r['cpu_cores']}cpu — {r['status']}")
    await ctx.send("Your VPS:\n" + "\n".join(lines))

@bot.command(name="manage")
async def cmd_manage(ctx, action:str, vps_id:int):
    row = get_vps_by_id(vps_id)
    if not row: return await ctx.send("VPS not found.")
    if row['user_id'] != ctx.author.id and not is_admin_ctx(ctx): return await ctx.send("You don't own this VPS.")
    name = row['container_name']
    action = action.lower()
    if action == "delete":
        await ctx.send(f"Deleting `{name}`...")
        ok,msg = await delete_container(name)
        if not ok: return await ctx.send(f"Failed to delete: ```{msg}```")
        delete_vps_record(vps_id)
        return await ctx.send(f"✅ VPS {vps_id} (`{name}`) deleted.")
    if action not in ("start","stop","restart","info"):
        return await ctx.send("Invalid action. Use start|stop|restart|delete|info")
    ok,msg = await action_container(name, action)
    if not ok: return await ctx.send(f"Action failed: ```{msg}```")
    if action=="info":
        return await ctx.send(f"Info for `{name}`:\n```\n{msg[:1900]}\n```")
    return await ctx.send(f"Action `{action}` executed on `{name}`.")

# ---------- Admin commands ----------
@bot.command(name="create")
async def cmd_create(ctx, user:discord.Member, ram_mb:int, cpu_cores:int):
    if not is_admin_ctx(ctx): return await ctx.send("You are not authorized.")
    base=f"user{user.id}-custom"
    safe=NAME_RE.sub("", base.lower())
    suffix=datetime.utcnow().strftime("%y%m%d%H%M%S")
    container_name=f"{safe}-{suffix}"
    await ctx.send(f"Creating `{container_name}` for {user.mention} ({ram_mb}MB/{cpu_cores}cpu)...")
    ok,msg = await create_container(container_name, image="images:ubuntu/22.04", profiles="default", ram_mb=ram_mb, cpu_cores=cpu_cores)
    if not ok: return await ctx.send(f"Failed: ```{msg}```")
    create_vps_record(user.id, container_name, "custom", ram_mb, cpu_cores, "intel")
    await ctx.send(f"✅ Created `{container_name}` for {user.mention}.")

@bot.command(name="delete-vps")
async def cmd_delete_vps(ctx, user:discord.Member, vps_number:int, *, reason:str="admin_deleted"):
    if not is_admin_ctx(ctx): return await ctx.send("You are not authorized.")
    row=get_vps_by_id(vps_number)
    if not row or row['user_id']!=user.id: return await ctx.send("VPS not found for that user/id.")
    name=row['container_name']
    await ctx.send(f"Deleting `{name}` for {user.mention} (reason: {reason})...")
    ok,msg = await delete_container(name)
    if not ok: return await ctx.send(f"Failed: ```{msg}```")
    delete_vps_record(vps_number)
    await ctx.send(f"✅ Deleted {name}.")

@bot.command(name="adminc")
async def cmd_adminc(ctx, user:discord.Member, amount:int):
    if not is_admin_ctx(ctx): return await ctx.send("You are not authorized.")
    add_credits(user.id, amount)
    await ctx.send(f"✅ Added {amount} credits to {user.mention}.")

@bot.command(name="adminrc")
async def cmd_adminrc(ctx, user:discord.Member, amount:str):
    if not is_admin_ctx(ctx): return await ctx.send("You are not authorized.")
    if amount=="all":
        cur=conn.cursor(); cur.execute("UPDATE users SET credits=0 WHERE user_id=?",(user.id,)); conn.commit()
        return await ctx.send(f"✅ Removed all credits from {user.mention}.")
    try:
        amt=int(amount)
    except:
        return await ctx.send("Invalid amount")
    remove_credits(user.id, amt)
    await ctx.send(f"✅ Removed {amt} credits from {user.mention}.")

@bot.command(name="editplans")
async def cmd_editplans(ctx, plan_name:str, ram_mb:int, cpu:int, disk_gb:int):
    if not is_admin_ctx(ctx): return await ctx.send("You are not authorized.")
    key=plan_name.lower()
    if key not in PLANS:
        return await ctx.send("Plan not found.")
    PLANS[key]['ram_mb']=ram_mb
    PLANS[key]['cpu']=cpu
    PLANS[key]['disk_gb']=disk_gb
    save_plans()
    await ctx.send(f"✅ Plan `{key}` updated: {ram_mb}MB RAM, {cpu} CPU, {disk_gb}GB disk.")

@bot.command(name="giveplan")
async def cmd_giveplan(ctx, user:discord.Member, plan_name:str):
    if not is_admin_ctx(ctx): return await ctx.send("You are not authorized.")
    key=plan_name.lower()
    if key not in PLANS: return await ctx.send("Plan not found.")
    plan=PLANS[key]
    # create container for user with plan resources
    base=f"user{user.id}-{key}"
    safe=NAME_RE.sub("", base.lower())
    suffix=datetime.utcnow().strftime("%y%m%d%H%M%S")
    container_name=f"{safe}-{suffix}"
    await ctx.send(f"Creating `{container_name}` for {user.mention} ({plan['ram_mb']}MB/{plan['cpu']}cpu)...")
    ok,msg = await create_container(container_name, ram_mb=plan['ram_mb'], cpu_cores=plan['cpu'])
    if not ok: return await ctx.send(f"Failed: ```{msg}```")
    create_vps_record(user.id, container_name, key, plan['ram_mb'], plan['cpu'], "intel")
    await ctx.send(f"✅ Gave plan `{key}` to {user.mention}. Container: `{container_name}`")

# ---------- Error handler ----------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument. Check command usage.")
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send("Command not found.")
    else:
        await ctx.send(f"Error: {error}")
        raise error

# ---------- Run ----------
if __name__ == "__main__":
    bot.run(TOKEN)
