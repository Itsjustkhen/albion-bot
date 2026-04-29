import os
import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import json

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

DB = "parties.db"

# ==== END LOGS CHANNEL HERE ====
LOG_CHANNEL_ID = 1498946116480143370
# ==============================

EMOJIS = {
    "Tank": "🛡️",
    "Healer": "💚",
    "DPS": "⚔️",
    "Player": "👤",
    "Ganker": "🗡️",
    "Caller": "📢",
    "Scout": "👀"
}

# ================= DATABASE =================
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS parties (
            message_id INTEGER PRIMARY KEY,
            creator_id INTEGER,
            dungeon TEXT,
            roles TEXT
        )
        """)
        await db.commit()

# ================= VIEW =================
class PartyView(discord.ui.View):
    def __init__(self, message_id):
        super().__init__(timeout=None)
        self.message_id = message_id

    async def get_data(self):
        async with aiosqlite.connect(DB) as db:
            cursor = await db.execute(
                "SELECT dungeon, roles FROM parties WHERE message_id=?",
                (self.message_id,))
            return await cursor.fetchone()

    def is_full(self, roles):
        return all(len(r["players"]) >= r["limit"] for r in roles.values())

    async def update_embed(self, interaction):
        data = await self.get_data()
        if not data:
            return

        dungeon, roles_raw = data
        roles = json.loads(roles_raw)

        missing = []
        for role, info in roles.items():
            need = info["limit"] - len(info["players"])
            if need > 0:
                missing.append(f"{need} {role}")

        missing_text = f"\n\n🔍 Need: {', '.join(missing)}" if missing else "\n\n✅ Party Full"

        embed = discord.Embed(
            title=f"⚔️ LFG: {dungeon}",
            description="Click to join" + missing_text,
            color=0x2ecc71
        )

        for role, info in roles.items():
            emoji = EMOJIS.get(role, "👤")
            players = "\n".join([f"{emoji} {p}" for p in info["players"]]) if info["players"] else "➖ Empty"

            embed.add_field(
                name=f"{emoji} {role} ({len(info['players'])}/{info['limit']})",
                value=players,
                inline=False
            )

        if self.is_full(roles):
            for item in self.children:
                item.disabled = True

        await interaction.message.edit(embed=embed, view=self)

    def build(self, roles):
        self.clear_items()
        for role in roles:
            self.add_item(RoleButton(role, self.message_id))
        self.add_item(LeaveButton(self.message_id))
        self.add_item(ManageButton(self.message_id))
        self.add_item(EndSessionButton(self.message_id))

# ================= ROLE BUTTON =================
class RoleButton(discord.ui.Button):
    def __init__(self, role, message_id):
        super().__init__(label=role, style=discord.ButtonStyle.primary)
        self.role = role
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        async with aiosqlite.connect(DB) as db:
            cursor = await db.execute("SELECT roles FROM parties WHERE message_id=?", (self.message_id,))
            roles = json.loads((await cursor.fetchone())[0])

            for r in roles:
                if interaction.user.name in roles[r]["players"]:
                    roles[r]["players"].remove(interaction.user.name)

            if len(roles[self.role]["players"]) >= roles[self.role]["limit"]:
                await interaction.followup.send("Role is full!", ephemeral=True)
                return

            roles[self.role]["players"].append(interaction.user.name)

            await db.execute("UPDATE parties SET roles=? WHERE message_id=?",
                             (json.dumps(roles), self.message_id))
            await db.commit()

        view = PartyView(self.message_id)
        view.build(roles)
        await view.update_embed(interaction)

# ================= LEAVE =================
class LeaveButton(discord.ui.Button):
    def __init__(self, message_id):
        super().__init__(label="Leave", style=discord.ButtonStyle.secondary)
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        async with aiosqlite.connect(DB) as db:
            cursor = await db.execute("SELECT roles FROM parties WHERE message_id=?", (self.message_id,))
            roles = json.loads((await cursor.fetchone())[0])

            for r in roles:
                if interaction.user.name in roles[r]["players"]:
                    roles[r]["players"].remove(interaction.user.name)

            await db.execute("UPDATE parties SET roles=? WHERE message_id=?",
                             (json.dumps(roles), self.message_id))
            await db.commit()

        view = PartyView(self.message_id)
        view.build(roles)
        await view.update_embed(interaction)

# ================= MANAGE =================
class ManageButton(discord.ui.Button):
    def __init__(self, message_id):
        super().__init__(label="Manage", style=discord.ButtonStyle.secondary)
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB) as db:
            cursor = await db.execute("SELECT creator_id, roles FROM parties WHERE message_id=?", (self.message_id,))
            creator_id, roles_raw = await cursor.fetchone()

        if interaction.user.id != creator_id:
            await interaction.response.send_message("Only creator.", ephemeral=True)
            return

        roles = json.loads(roles_raw)
        options = [discord.SelectOption(label=p, description=r)
                   for r in roles for p in roles[r]["players"]]

        if not options:
            await interaction.response.send_message("No members.", ephemeral=True)
            return

        await interaction.response.send_message("Remove:", view=KickView(self.message_id, options), ephemeral=True)

class KickView(discord.ui.View):
    def __init__(self, message_id, options):
        super().__init__(timeout=30)
        self.add_item(KickSelect(message_id, options))

class KickSelect(discord.ui.Select):
    def __init__(self, message_id, options):
        super().__init__(placeholder="Select member...", options=options)
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        async with aiosqlite.connect(DB) as db:
            cursor = await db.execute("SELECT roles FROM parties WHERE message_id=?", (self.message_id,))
            roles = json.loads((await cursor.fetchone())[0])

            selected = self.values[0]

            for r in roles:
                if selected in roles[r]["players"]:
                    roles[r]["players"].remove(selected)

            await db.execute("UPDATE parties SET roles=? WHERE message_id=?",
                             (json.dumps(roles), self.message_id))
            await db.commit()

        view = PartyView(self.message_id)
        view.build(roles)
        await view.update_embed(interaction)

# ================= END SESSION =================
class EndSessionButton(discord.ui.Button):
    def __init__(self, message_id):
        super().__init__(label="End Session", style=discord.ButtonStyle.danger)
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB) as db:
            cursor = await db.execute("SELECT creator_id, dungeon, roles FROM parties WHERE message_id=?", (self.message_id,))
            creator_id, dungeon, roles_raw = await cursor.fetchone()

        if interaction.user.id != creator_id:
            await interaction.response.send_message("Only creator.", ephemeral=True)
            return

        roles = json.loads(roles_raw)

        embed = discord.Embed(title=f"📜 Session Ended: {dungeon}", color=0xe74c3c)

        for role, info in roles.items():
            players = "\n".join(info["players"]) if info["players"] else "None"
            embed.add_field(name=role, value=players, inline=False)

        await interaction.response.defer()
        await interaction.message.edit(embed=embed, view=None)

        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(embed=embed)

        async with aiosqlite.connect(DB) as db:
            await db.execute("DELETE FROM parties WHERE message_id=?", (self.message_id,))
            await db.commit()

# ================= MODAL =================
class SetupModal(discord.ui.Modal):
    def __init__(self, mode):
        super().__init__(title=f"Create {mode.capitalize()} Party")
        self.mode = mode

    dungeon = discord.ui.TextInput(label="Content Name")
    tank = discord.ui.TextInput(label="Tank Count", default="1", required=False)
    healer = discord.ui.TextInput(label="Healer Count", default="1", required=False)
    dps = discord.ui.TextInput(label="DPS Count", default="3", required=False)
    extra = discord.ui.TextInput(label="Extra Roles (Caller:2)", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        if self.mode == "dungeon":
            roles = {
                "Tank": {"limit": int(self.tank.value or 1), "players": []},
                "Healer": {"limit": int(self.healer.value or 1), "players": []},
                "DPS": {"limit": int(self.dps.value or 3), "players": []}
            }
        elif self.mode == "depths":
            roles = {"Player": {"limit": 3, "players": []}}
        else:
            roles = {"Ganker": {"limit": 5, "players": []}}

        if self.extra.value:
            for r in self.extra.value.split(","):
                name, count = (r.split(":") + ["1"])[:2]
                roles[name.strip()] = {"limit": int(count.strip()), "players": []}

        embed = discord.Embed(
            title=f"⚔️ LFG: {self.dungeon.value}",
            description="Click to join",
            color=0x2ecc71
        )

        for role, info in roles.items():
            emoji = EMOJIS.get(role, "👤")
            embed.add_field(name=f"{emoji} {role} (0/{info['limit']})", value="➖ Empty", inline=False)

        message = await interaction.followup.send(embed=embed)

        view = PartyView(message.id)
        view.build(roles)
        await message.edit(view=view)

        async with aiosqlite.connect(DB) as db:
            await db.execute("INSERT INTO parties VALUES (?, ?, ?, ?)",
                             (message.id, interaction.user.id, self.dungeon.value, json.dumps(roles)))
            await db.commit()

# ================= COMMAND =================
@bot.tree.command(name="lfg")
@app_commands.choices(mode=[
    app_commands.Choice(name="Dungeon", value="dungeon"),
    app_commands.Choice(name="Depths", value="depths"),
    app_commands.Choice(name="Ganking", value="ganking")
])
@app_commands.checks.has_any_role("Guild Leader", "Officer")
async def lfg(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    await interaction.response.send_modal(SetupModal(mode.value))

@bot.event
async def on_ready():
    await init_db()
    await bot.tree.sync()
    print("Bot ready")


bot.run(os.getenv("TOKEN"))
