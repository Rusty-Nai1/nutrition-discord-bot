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
    
    async def safe_defer(self, interaction: discord.Interaction) -> bool:
        """Safely defer interaction with proper error handling"""
        if interaction.response.is_done():
            logger.info(f"Interaction {interaction.id} already responded, skipping defer")
            return False
        
        try:
            await interaction.response.defer()
            logger.info(f"Successfully deferred interaction {interaction.id}")
            return True
        except discord.NotFound:
            logger.error(f"Interaction {interaction.id} not found (expired token)")
            return False
        except discord.HTTPException as e:
            logger.error(f"HTTP error deferring interaction {interaction.id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deferring interaction {interaction.id}: {e}")
            return False
    
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
    
    async def safe_send_followup(self, interaction: discord.Interaction, **kwargs) -> bool:
        """Safely send followup message with error handling"""
        try:
            await interaction.followup.send(**kwargs)
            logger.info("Successfully sent followup message")
            return True
        except discord.NotFound:
            logger.error("Followup failed: Interaction not found (expired token)")
            return False
        except discord.HTTPException as e:
            logger.error(f"HTTP error in followup: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in followup: {e}")
            return False
    
    async def safe_send_modal(self, interaction: discord.Interaction, modal: discord.ui.Modal) -> bool:
        """Safely send modal with error handling"""
        if interaction.response.is_done():
            logger.error("Cannot send modal - interaction already responded")
            return False
        
        try:
            await interaction.response.send_modal(modal)
            logger.info("Successfully sent modal")
            return True
        except discord.NotFound:
            logger.error("Modal send failed: Interaction not found (expired token)")
            return False
        except discord.HTTPException as e:
            logger.error(f"HTTP error sending modal: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending modal: {e}")
            return False
    
    async def handle_interaction(self, interaction: discord.Interaction):
        try:
            # Serialize the raw Discord interaction
            interaction_data = self.serialize_interaction(interaction)
            logger.info(f"Handling interaction {interaction.id} type {interaction.type}")
            
            # Forward to Lambda
            lambda_response = await self.call_lambda(interaction_data)
            logger.info(f"Lambda response received for interaction {interaction.id}")
            
            # Process Lambda response
            response_type = lambda_response.get('type', 4)
            logger.info(f"Processing response type: {response_type}")
            
            # Handle modal responses first (cannot be deferred)
            if response_type == 9:  # MODAL
                logger.info("Processing MODAL response")
                modal_data = lambda_response.get('data', {})
                modal = self.create_modal_from_data(modal_data)
                await self.safe_send_modal(interaction, modal)
                return
            
            # For all other responses, try to defer first
            deferred = await self.safe_defer(interaction)
            if not deferred and not interaction.response.is_done():
                # If defer failed and no response sent, send error
                try:
                    embed = discord.Embed(
                        title="❌ Interaction Expired",
                        description="This interaction has expired. Please try again.",
                        color=0xff4444
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                except:
                    logger.error("Failed to send expiry message")
                return
            
            # Process different response types
            if response_type == 4:  # CHANNEL_MESSAGE_WITH_SOURCE
                logger.info("Processing CHANNEL_MESSAGE_WITH_SOURCE response")
                data = lambda_response.get('data', {})
                content = data.get('content', '')
                
                embeds = []
                if 'embeds' in data:
                    for i, embed_data in enumerate(data['embeds']):
                        try:
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
                        except Exception as e:
                            logger.error(f"Error creating embed {i}: {e}")
                
                view = None
                if 'components' in data:
                    try:
                        view = self.create_view_from_components(data['components'])
                    except Exception as e:
                        logger.error(f"Error creating view: {e}")
                
                ephemeral = data.get('flags', 0) & 64 == 64
                
                # Send followup if deferred, otherwise try response
                if deferred:
                    await self.safe_send_followup(
                        interaction, 
                        content=content, 
                        embeds=embeds, 
                        view=view, 
                        ephemeral=ephemeral
                    )
                else:
                    try:
                        await interaction.response.send_message(
                            content=content, 
                            embeds=embeds, 
                            view=view, 
                            ephemeral=ephemeral
                        )
                    except Exception as e:
                        logger.error(f"Error sending response message: {e}")
            
            elif response_type == 7:  # UPDATE_MESSAGE
                logger.info("Processing UPDATE_MESSAGE response")
                data = lambda_response.get('data', {})
                content = data.get('content', '')
                
                embeds = []
                if 'embeds' in data:
                    for embed_data in data['embeds']:
                        try:
                            embed = discord.Embed(
                                title=embed_data.get('title'),
                                description=embed_data.get('description'),
                                color=embed_data.get('color', 0x0099ff)
                            )
                            embeds.append(embed)
                        except Exception as e:
                            logger.error(f"Error creating update embed: {e}")
                
                view = None
                if 'components' in data:
                    try:
                        view = self.create_view_from_components(data['components'])
                    except Exception as e:
                        logger.error(f"Error creating update view: {e}")
                
                try:
                    await interaction.edit_original_response(content=content, embeds=embeds, view=view)
                    logger.info("Successfully updated original message")
                except discord.NotFound:
                    logger.error("Update failed: Original message not found")
                except Exception as e:
                    logger.error(f"Error updating message: {e}")
            
            logger.info(f"Successfully handled interaction {interaction.id}")
            
        except TimeoutError:
            logger.error(f"Timeout handling interaction {interaction.id}")
            if not interaction.response.is_done():
                try:
                    embed = discord.Embed(
                        title="⏰ Timeout",
                        description="Request timed out. Please try again!",
                        color=0xff9900
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                except:
                    pass
            
        except Exception as e:
            logger.error(f"Error handling interaction {interaction.id}: {e}")
            if not interaction.response.is_done():
                try:
                    embed = discord.Embed(
                        title="❌ Error",
                        description="Something went wrong. Please try again later!",
                        color=0xff4444
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                except:
                    pass

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
