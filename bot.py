import discord
from discord.ext import commands
import requests
import json
import asyncio
import os
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
LAMBDA_API_URL = os.getenv('LAMBDA_API_URL', 'https://qv2c3tpjl2.execute-api.us-east-2.amazonaws.com/prod/nutrition')

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable required")

class NutritionBot:
    def __init__(self):
        self.api_timeout = 15
        self.max_retries = 2
    
    async def call_lambda_api(self, command: str, details: str) -> str:
        """Call Lambda nutrition API with error handling"""
        payload = {"command": command, "details": details}
        headers = {"Content-Type": "application/json"}
        
        for attempt in range(self.max_retries + 1):
            try:
                logger.info(f"API call attempt {attempt + 1}: {command}")
                
                response = requests.post(
                    LAMBDA_API_URL,
                    json=payload,
                    headers=headers,
                    timeout=self.api_timeout
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return data.get('response', 'No response received')
                else:
                    logger.error(f"API returned status {response.status_code}")
                    raise requests.exceptions.RequestException(f"API error: {response.status_code}")
                    
            except requests.exceptions.Timeout:
                logger.warning(f"API timeout on attempt {attempt + 1}")
                if attempt == self.max_retries:
                    raise TimeoutError("API request timed out")
                await asyncio.sleep(2)
                
            except requests.exceptions.RequestException as e:
                logger.error(f"API request failed: {e}")
                if attempt == self.max_retries:
                    raise
                await asyncio.sleep(2)
    
    def create_nutrition_embed(self, command: str, advice: str, details: str) -> discord.Embed:
        """Create formatted embed for nutrition advice"""
        colors = {
            'preshift': 0x00ff00,    # Green
            'break': 0x0099ff,       # Blue  
            'recovery': 0xff6600,    # Orange
            'meal_prep': 0x9900ff    # Purple
        }
        
        emojis = {
            'preshift': '⚡',
            'break': '☕',
            'recovery': '🏥',
            'meal_prep': '📋'
        }
        
        embed = discord.Embed(
            title=f"{emojis.get(command, '🍎')} {command.replace('_', ' ').title()} Nutrition Guide",
            description=advice,
            color=colors.get(command, 0x00ff88),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="Your Details", value=details, inline=False)
        embed.set_footer(text="Stay healthy! 💪 | Powered by Claude AI")
        
        return embed
    
    def create_error_embed(self, title: str, description: str) -> discord.Embed:
        """Create error embed"""
        return discord.Embed(
            title=f"❌ {title}",
            description=description,
            color=0xff4444,
            timestamp=datetime.utcnow()
        )

nutrition_bot = NutritionBot()

@bot.event
async def on_ready():
    logger.info(f'{bot.user} connected to Discord!')
    logger.info(f'Bot is in {len(bot.guilds)} servers')
    
    try:
        synced = await bot.tree.sync()
        logger.info(f'Synced {len(synced)} slash commands')
    except Exception as e:
        logger.error(f'Failed to sync commands: {e}')
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="for /preshift, /break, /recovery, /meal_prep"
        )
    )

async def handle_nutrition_command(interaction: discord.Interaction, command: str, details: str):
    """Handle nutrition command with error handling"""
    
    if not details or len(details.strip()) < 3:
        embed = nutrition_bot.create_error_embed(
            "Missing Details",
            f"Please provide details for your {command.replace('_', ' ')} request.\n\n"
            f"Example: `/{command} 10 hour night shift, need energy boost`"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        advice = await nutrition_bot.call_lambda_api(command, details)
        embed = nutrition_bot.create_nutrition_embed(command, advice, details)
        await interaction.followup.send(embed=embed)
        logger.info(f"Successfully handled {command} for {interaction.user}")
        
    except TimeoutError:
        embed = nutrition_bot.create_error_embed(
            "Request Timeout", 
            "Nutrition bot is thinking... Please try again! 🤔"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"Error handling {command}: {e}")
        embed = nutrition_bot.create_error_embed(
            "Service Unavailable",
            "Bot having issues. Please try again later! 🔧"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="preshift", description="Get nutrition advice before your shift")
@discord.app_commands.describe(details="Describe your shift (length, type, energy needs)")
async def preshift(interaction: discord.Interaction, details: str):
    await handle_nutrition_command(interaction, "preshift", details)

@bot.tree.command(name="break", description="Get nutrition suggestions for your break")
@discord.app_commands.describe(details="Describe your break and current state")
async def break_cmd(interaction: discord.Interaction, details: str):
    await handle_nutrition_command(interaction, "break", details)

@bot.tree.command(name="recovery", description="Get post-shift recovery nutrition advice")
@discord.app_commands.describe(details="Describe your shift and how you're feeling")
async def recovery(interaction: discord.Interaction, details: str):
    await handle_nutrition_command(interaction, "recovery", details)

@bot.tree.command(name="meal_prep", description="Get meal preparation planning advice")
@discord.app_commands.describe(details="Describe your work schedule and meal prep needs")
async def meal_prep(interaction: discord.Interaction, details: str):
    await handle_nutrition_command(interaction, "meal_prep", details)

@bot.tree.command(name="help", description="Show all available nutrition commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🍎 Nutrition Bot Commands",
        description="Get personalized nutrition advice for your work schedule!",
        color=0x0099ff,
        timestamp=datetime.utcnow()
    )
    
    commands_info = [
        ("**/preshift [details]**", "Pre-shift nutrition advice\nExample: `/preshift 10 hour stow shift, need sustained energy`"),
        ("**/break [details]**", "Break nutrition suggestions\nExample: `/break 15 min break, feeling tired`"),
        ("**/recovery [details]**", "Post-shift recovery advice\nExample: `/recovery finished 12 hour shift, exhausted`"),
        ("**/meal_prep [details]**", "Meal prep planning\nExample: `/meal_prep Sunday prep for 5 day work week`")
    ]
    
    for name, value in commands_info:
        embed.add_field(name=name, value=value, inline=False)
    
    embed.set_footer(text="Powered by Claude AI 🤖")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_command_error(ctx, error):
    logger.error(f"Command error: {error}")

@bot.event  
async def on_error(event, *args, **kwargs):
    logger.error(f"Discord error in {event}")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)