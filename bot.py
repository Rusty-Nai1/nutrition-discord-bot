import discord
from discord.ext import commands
import requests
import json
import asyncio
import os
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
LAMBDA_ENDPOINT = os.getenv('LAMBDA_ENDPOINT')

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable required")
if not LAMBDA_ENDPOINT:
    raise ValueError("LAMBDA_ENDPOINT environment variable required")

class InteractionHandler:
    def __init__(self):
        self.api_timeout = 30
        self.max_retries = 2
    
    def serialize_interaction(self, interaction: discord.Interaction) -> dict:
        """Convert Discord interaction to JSON-serializable format"""
        data = {
            "id": str(interaction.id),
            "application_id": str(interaction.application_id),
            "type": interaction.type.value,
            "token": interaction.token,
            "version": 1,
            "channel_id": str(interaction.channel_id) if interaction.channel_id else None,
            "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
            "user": {
                "id": str(interaction.user.id),
                "username": interaction.user.name,
                "discriminator": interaction.user.discriminator,
                "display_name": interaction.user.display_name,
                "bot": interaction.user.bot
            },
            "member": {
                "nick": interaction.user.nick if hasattr(interaction.user, 'nick') else None
            } if interaction.guild else None,
            "data": {}
        }
        
        if hasattr(interaction, 'data') and interaction.data:
            if interaction.type == discord.InteractionType.application_command:
                data["data"] = {
                    "id": str(interaction.data.get("id", "")),
                    "name": interaction.data.get("name", ""),
                    "type": interaction.data.get("type", 1),
                    "options": interaction.data.get("options", [])
                }
            elif interaction.type == discord.InteractionType.component:
                data["data"] = {
                    "custom_id": interaction.data.get("custom_id", ""),
                    "component_type": interaction.data.get("component_type", 2)
                }
            elif interaction.type == discord.InteractionType.modal_submit:
                data["data"] = {
                    "custom_id": interaction.data.get("custom_id", ""),
                    "components": interaction.data.get("components", [])
                }
        
        return data
    
    async def call_lambda(self, interaction_data: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        
        for attempt in range(self.max_retries + 1):
            try:
                logger.info(f"Lambda call attempt {attempt + 1}")
                
                response = requests.post(
                    LAMBDA_ENDPOINT,
                    json=interaction_data,
                    headers=headers,
                    timeout=self.api_timeout
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if 'body' in data:
                        return json.loads(data['body'])
                    return data
                else:
                    logger.error(f"Lambda returned status {response.status_code}")
                    raise requests.exceptions.RequestException(f"Lambda error: {response.status_code}")
                    
            except requests.exceptions.Timeout:
                logger.warning(f"Lambda timeout on attempt {attempt + 1}")
                if attempt == self.max_retries:
                    raise TimeoutError("Lambda request timed out")
                await asyncio.sleep(2)
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Lambda request failed: {e}")
                if attempt == self.max_retries:
                    raise
                await asyncio.sleep(2)
    
    def create_view_from_components(self, components: list) -> discord.ui.View:
        view = discord.ui.View(timeout=300)
        
        for component_row in components:
            if component_row.get('type') == 1:  # Action Row
                for component in component_row.get('components', []):
                    if component.get('type') == 2:  # Button
                        button = discord.ui.Button(
                            style=getattr(discord.ButtonStyle, 
                                        ['primary', 'secondary', 'success', 'danger', 'link'][component.get('style', 1) - 1]),
                            label=component.get('label', ''),
                            custom_id=component.get('custom_id'),
                            emoji=component.get('emoji', {}).get('name') if component.get('emoji') else None,
                            disabled=component.get('disabled', False)
                        )
                        button.callback = self.create_button_callback(component.get('custom_id'))
                        view.add_item(button)
        
        return view
    
    def create_button_callback(self, custom_id: str):
        async def button_callback(interaction: discord.Interaction):
            await self.handle_interaction(interaction)
        return button_callback
    
    def create_modal_from_data(self, modal_data: dict) -> discord.ui.Modal:
        class DynamicModal(discord.ui.Modal):
            def __init__(self, modal_data, handler):
                super().__init__(title=modal_data.get('title', 'Modal'))
                self.handler = handler
                self.custom_id = modal_data.get('custom_id', '')
                
                for component_row in modal_data.get('components', []):
                    if component_row.get('type') == 1:  # Action Row
                        for component in component_row.get('components', []):
                            if component.get('type') == 4:  # Text Input
                                text_input = discord.ui.TextInput(
                                    label=component.get('label', ''),
                                    placeholder=component.get('placeholder', ''),
                                    required=component.get('required', True),
                                    max_length=component.get('max_length', 4000),
                                    style=discord.TextStyle.short if component.get('style', 1) == 1 else discord.TextStyle.paragraph
                                )
                                text_input.custom_id = component.get('custom_id', '')
                                self.add_item(text_input)
            
            async def on_submit(self, interaction: discord.Interaction):
                await self.handler.handle_interaction(interaction)
        
        return DynamicModal(modal_data, self)
    
    async def handle_interaction(self, interaction: discord.Interaction):
        try:
            # Serialize the raw Discord interaction
            interaction_data = self.serialize_interaction(interaction)
            
            # Forward to Lambda
            lambda_response = await self.call_lambda(interaction_data)
            
            # Process Lambda response
            response_type = lambda_response.get('type', 4)
            
            if response_type == 4:  # CHANNEL_MESSAGE_WITH_SOURCE
                data = lambda_response.get('data', {})
                content = data.get('content', '')
                
                embeds = []
                if 'embeds' in data:
                    for embed_data in data['embeds']:
                        embed = discord.Embed(
                            title=embed_data.get('title'),
                            description=embed_data.get('description'),
                            color=embed_data.get('color', 0x0099ff)
                        )
                        if 'fields' in embed_data:
                            for field in embed_data['fields']:
                                embed.add_field(
                                    name=field.get('name', ''),
                                    value=field.get('value', ''),
                                    inline=field.get('inline', False)
                                )
                        embeds.append(embed)
                
                view = None
                if 'components' in data:
                    view = self.create_view_from_components(data['components'])
                
                ephemeral = data.get('flags', 0) & 64 == 64
                
                if interaction.response.is_done():
                    if embeds:
                        await interaction.followup.send(content=content, embeds=embeds, view=view, ephemeral=ephemeral)
                    else:
                        await interaction.followup.send(content=content, view=view, ephemeral=ephemeral)
                else:
                    if embeds:
                        await interaction.response.send_message(content=content, embeds=embeds, view=view, ephemeral=ephemeral)
                    else:
                        await interaction.response.send_message(content=content, view=view, ephemeral=ephemeral)
            
            elif response_type == 9:  # MODAL
                modal_data = lambda_response.get('data', {})
                modal = self.create_modal_from_data(modal_data)
                await interaction.response.send_modal(modal)
            
            elif response_type == 5:  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
                if not interaction.response.is_done():
                    await interaction.response.defer()
            
            elif response_type == 6:  # DEFERRED_UPDATE_MESSAGE
                if not interaction.response.is_done():
                    await interaction.response.defer()
            
            elif response_type == 7:  # UPDATE_MESSAGE
                data = lambda_response.get('data', {})
                content = data.get('content', '')
                
                embeds = []
                if 'embeds' in data:
                    for embed_data in data['embeds']:
                        embed = discord.Embed(
                            title=embed_data.get('title'),
                            description=embed_data.get('description'),
                            color=embed_data.get('color', 0x0099ff)
                        )
                        embeds.append(embed)
                
                view = None
                if 'components' in data:
                    view = self.create_view_from_components(data['components'])
                
                if interaction.response.is_done():
                    if embeds:
                        await interaction.edit_original_response(content=content, embeds=embeds, view=view)
                    else:
                        await interaction.edit_original_response(content=content, view=view)
                else:
                    if embeds:
                        await interaction.response.edit_message(content=content, embeds=embeds, view=view)
                    else:
                        await interaction.response.edit_message(content=content, view=view)
            
            logger.info(f"Successfully handled interaction for {interaction.user}")
            
        except TimeoutError:
            if not interaction.response.is_done():
                embed = discord.Embed(
                    title="⏰ Timeout",
                    description="Request timed out. Please try again!",
                    color=0xff9900
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error handling interaction: {e}")
            if not interaction.response.is_done():
                embed = discord.Embed(
                    title="❌ Error",
                    description="Something went wrong. Please try again later!",
                    color=0xff4444
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

handler = InteractionHandler()

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
            name="for /hi and /hola"
        )
    )

@bot.tree.command(name="hi", description="Start a conversation in English")
async def hi(interaction: discord.Interaction):
    await handler.handle_interaction(interaction)

@bot.tree.command(name="hola", description="Iniciar una conversación en español")
async def hola(interaction: discord.Interaction):
    await handler.handle_interaction(interaction)

@bot.event
async def on_command_error(ctx, error):
    logger.error(f"Command error: {error}")

@bot.event  
async def on_error(event, *args, **kwargs):
    logger.error(f"Discord error in {event}")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
