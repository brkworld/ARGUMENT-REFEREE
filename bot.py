import json
import os
import re

import discord
from anthropic import AsyncAnthropic
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
claude = AsyncAnthropic()  # reads ANTHROPIC_API_KEY from the environment

SYSTEM = """You are a sharp, fair, funny debate referee. You are given a chat transcript.
Identify the two main opposing sides (by speaker name). Judge ONLY quality of reasoning
and evidence, not who you like. Cite what was actually said. Name logical fallacies
precisely when present (strawman, whataboutism, moving the goalposts, ad hominem, etc.).
Scores are 0-100. If it is close, winner may be "tie". If there is no real argument in the
transcript, set "no_argument" to true.

Return ONLY JSON, no markdown:
{"no_argument":false,"winner":"a"|"b"|"tie","ruling":"2-3 sentences",
 "a":{"name":"","score":0,"strengths":["max 3 short"],"fallacies":["max 3 short, [] if none"]},
 "b":{"name":"","score":0,"strengths":[],"fallacies":[]},
 "closer":"one witty, kind-but-honest line"}"""


class Referee(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # also enable in the Developer Portal
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()


bot = Referee()


def bullets(items):
    text = "\n".join(f"• {x}" for x in (items or []))
    return (text or "None")[:1000]


def parse(raw: str) -> dict:
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    return json.loads(raw)


@bot.tree.command(name="referee", description="Referee the recent argument in this channel")
@app_commands.describe(messages="How many recent messages to review (5-50, default 20)")
@app_commands.checks.cooldown(1, 30.0, key=lambda i: i.user.id)
async def referee(interaction: discord.Interaction, messages: app_commands.Range[int, 5, 50] = 20):
    await interaction.response.defer()

    lines = []
    async for m in interaction.channel.history(limit=messages):
        if m.author.bot or not m.content.strip():
            continue
        lines.append(f"{m.author.display_name}: {m.content.strip()}")
    lines.reverse()

    if len(lines) < 3:
        await interaction.followup.send("Not enough messages to referee. Let the fight develop first.")
        return

    try:
        resp = await claude.messages.create(
            model=MODEL,
            max_tokens=1000,
            system=SYSTEM,
            messages=[{"role": "user", "content": "TRANSCRIPT:\n" + "\n".join(lines)[:12000]}],
        )
        r = parse(resp.content[0].text)
    except Exception as e:  # API error or bad JSON
        print("referee error:", repr(e))
        await interaction.followup.send("The referee dropped the whistle. Try again in a moment.")
        return

    if r.get("no_argument"):
        await interaction.followup.send("I do not see an actual argument here. Case dismissed.")
        return

    a, b = r["a"], r["b"]
    winner = {"a": a["name"], "b": b["name"]}.get(r["winner"])
    embed = discord.Embed(
        title=f"{winner} wins." if winner else "It is a draw.",
        description=r["ruling"],
        color=discord.Color.orange(),
    )
    for side in (a, b):
        embed.add_field(
            name=f"{side['name']}: {int(side['score'])}/100",
            value=f"**Strong points**\n{bullets(side['strengths'])}\n**Fouls**\n{bullets(side['fallacies'])}",
            inline=True,
        )
    embed.set_footer(text=r["closer"][:2000])
    await interaction.followup.send(embed=embed)


@referee.error
async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"The referee is catching their breath. Try again in {error.retry_after:.0f}s.", ephemeral=True
        )
    else:
        raise error


bot.run(os.environ["DISCORD_TOKEN"])
