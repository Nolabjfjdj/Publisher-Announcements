import os
import json
import asyncio
import threading
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATA_FILE = "active_channels.json"

# ---------- Tes serveurs ----------
# Sync des commandes slash directement sur ces guilds = disponible instantanément
# (la sync globale peut prendre jusqu'à 1h à se propager).
GUILD_IDS = [
    1525625680706932746,  # Serveur 1
    # 0,                  # Serveur 2 -> remplace le 0 par l'ID une fois que tu l'as
]

# Salons d'annonces à activer automatiquement au démarrage du bot
# (pas besoin de refaire /start à chaque redéploiement sur Render)
DEFAULT_ACTIVE_CHANNELS = {
    1526684169642709063,
    1525633582519943168,
}

# ---------- Serveur Flask pour le keep-alive (UptimeRobot) ----------
# Render (free tier) met en veille un Web Service après inactivité HTTP.
# On expose donc une petite route "/" que UptimeRobot va ping toutes les
# 5 minutes pour empêcher le service de s'endormir.
app = Flask(__name__)

@app.route("/")
def home():
    return "Auto Publisher bot en ligne."

def run_flask():
    port = int(os.environ.get("PORT", 8080))  # Render fournit PORT automatiquement
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

# ---------- Persistance (survit aux redémarrages du bot) ----------
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_data(active_channels: set):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(list(active_channels), f)

# ---------- Setup du bot ----------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
# Si active_channels.json existe déjà (redéploiement), on garde son contenu.
# Sinon on démarre avec les salons par défaut définis plus haut.
_loaded = load_data()
bot.active_channels = _loaded if _loaded else set(DEFAULT_ACTIVE_CHANNELS)
if not _loaded:
    save_data(bot.active_channels)


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user} (ID: {bot.user.id})")
    try:
        for guild_id in GUILD_IDS:
            if not guild_id:
                continue
            guild = discord.Object(id=guild_id)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"{len(synced)} commande(s) synchronisée(s) sur le serveur {guild_id}.")
    except Exception as e:
        print(f"Erreur de sync : {e}")


@bot.event
async def on_message(message: discord.Message):
    # Ignore les messages du bot lui-même
    if message.author.bot:
        return

    # Si le salon est actif, on publie automatiquement le message
    if message.channel.id in bot.active_channels:
        # Le crosspost ne marche que sur un salon de type Annonce (news)
        if isinstance(message.channel, discord.TextChannel) and message.channel.is_news():
            try:
                await message.publish()
            except discord.HTTPException as e:
                print(f"Impossible de publier le message {message.id} : {e}")
        else:
            # Le salon n'est plus/pas un salon d'annonces -> on désactive proprement
            bot.active_channels.discard(message.channel.id)
            save_data(bot.active_channels)
            try:
                await message.channel.send(
                    "⚠️ Ce salon n'est pas (ou plus) un salon d'annonces. "
                    "L'auto-publish a été désactivé. Convertis le salon en salon "
                    "**Annonces** dans les paramètres, puis refais `/start`."
                )
            except discord.HTTPException:
                pass

    await bot.process_commands(message)


# ---------- Commande /start ----------
@bot.tree.command(name="start", description="Active l'auto-publish (Auto Publisher) dans ce salon")
@app_commands.checks.has_permissions(manage_guild=True)
async def start(interaction: discord.Interaction):
    channel = interaction.channel

    if not isinstance(channel, discord.TextChannel) or not channel.is_news():
        await interaction.response.send_message(
            "❌ Ce salon n'est pas un salon d'annonces. "
            "Va dans **Paramètres du salon > Vue d'ensemble** et change le type en "
            "**Salon d'annonces** avant d'utiliser `/start`.",
            ephemeral=True
        )
        return

    if channel.id in bot.active_channels:
        await interaction.response.send_message(
            "ℹ️ L'auto-publish est déjà actif sur ce salon.",
            ephemeral=True
        )
        return

    bot.active_channels.add(channel.id)
    save_data(bot.active_channels)

    await interaction.response.send_message(
        "✅ Auto-publish **activé** sur ce salon. "
        "Tous les nouveaux messages seront automatiquement publiés (crosspost) "
        "vers les serveurs qui suivent ce salon."
    )


# ---------- Commande /stop ----------
@bot.tree.command(name="stop", description="Désactive l'auto-publish dans ce salon")
@app_commands.checks.has_permissions(manage_guild=True)
async def stop(interaction: discord.Interaction):
    channel = interaction.channel

    if channel.id not in bot.active_channels:
        await interaction.response.send_message(
            "ℹ️ L'auto-publish n'est pas actif sur ce salon.",
            ephemeral=True
        )
        return

    bot.active_channels.discard(channel.id)
    save_data(bot.active_channels)

    await interaction.response.send_message(
        "🛑 Auto-publish **désactivé** sur ce salon."
    )


# ---------- Commande /status (bonus, pratique) ----------
@bot.tree.command(name="status", description="Vérifie si l'auto-publish est actif dans ce salon")
async def status(interaction: discord.Interaction):
    active = interaction.channel.id in bot.active_channels
    state = "✅ activé" if active else "🛑 désactivé"
    await interaction.response.send_message(f"Auto-publish sur ce salon : {state}", ephemeral=True)


# ---------- Gestion des erreurs de permissions ----------
@start.error
@stop.error
async def permission_error_handler(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ Tu dois avoir la permission **Gérer le serveur** pour utiliser cette commande.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(f"❌ Erreur : {error}", ephemeral=True)
        raise error


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN manquant dans les variables d'environnement.")
    keep_alive()  # démarre le mini serveur Flask pour UptimeRobot
    bot.run(TOKEN)
