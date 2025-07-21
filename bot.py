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
            logger.info(f"Serialized interaction data: {json.dumps(interaction_data, indent=2)}")
            
            # Forward to Lambda
            lambda_response = await self.call_lambda(interaction_data)
            logger.info(f"Lambda response received: {json.dumps(lambda_response, indent=2)}")
            
            # Process Lambda response
            response_type = lambda_response.get('type', 4)
            logger.info(f"Processing response type: {response_type}")
            
            # CRITICAL FIX: Check response type BEFORE deferring
            if response_type == 9:  # MODAL - Cannot defer modal responses
                logger.info("Processing MODAL response")
                modal_data = lambda_response.get('data', {})
                logger.info(f"Modal data: {modal_data}")
                try:
                    modal = self.create_modal_from_data(modal_data)
                    await interaction.response.send_modal(modal)
                    logger.info("Successfully sent modal")
                except Exception as e:
                    logger.error(f"Error creating/sending modal: {e}")
                    logger.error(f"Modal data: {modal_data}")
                return
            
            # DEFER for all non-modal responses
            if not interaction.response.is_done():
                await interaction.response.defer()
                logger.info(f"Deferred interaction {interaction.id}")
            
            if response_type == 4:  # CHANNEL_MESSAGE_WITH_SOURCE
                logger.info("Processing CHANNEL_MESSAGE_WITH_SOURCE response")
                data = lambda_response.get('data', {})
                content = data.get('content', '')
                logger.info(f"Message content: {content}")
                
                embeds = []
                if 'embeds' in data:
                    logger.info(f"Processing {len(data['embeds'])} embeds")
                    for i, embed_data in enumerate(data['embeds']):
                        logger.info(f"Processing embed {i}: {embed_data}")
                        try:
                            embed = discord.Embed(
                                title=embed_data.get('title'),
                                description=embed_data.get('description'),
                                color=embed_data.get('color', 0x0099ff)
                            )
                            if 'fields' in embed_data:
                                logger.info(f"Adding {len(embed_data['fields'])} fields to embed {i}")
                                for field in embed_data['fields']:
                                    embed.add_field(
                                        name=field.get('name', ''),
                                        value=field.get('value', ''),
                                        inline=field.get('inline', False)
                                    )
                            embeds.append(embed)
                            logger.info(f"Successfully created embed {i}")
                        except Exception as e:
                            logger.error(f"Error creating embed {i}: {e}")
                            logger.error(f"Embed data: {embed_data}")
                
                view = None
                if 'components' in data:
                    logger.info(f"Processing {len(data['components'])} component rows")
                    try:
                        view = self.create_view_from_components(data['components'])
                        logger.info("Successfully created view from components")
                    except Exception as e:
                        logger.error(f"Error creating view: {e}")
                        logger.error(f"Components data: {data['components']}")
                
                ephemeral = data.get('flags', 0) & 64 == 64
                logger.info(f"Ephemeral: {ephemeral}")
                
                try:
                    if embeds:
                        logger.info(f"Sending followup with {len(embeds)} embeds and view={view is not None}")
                        await interaction.followup.send(content=content, embeds=embeds, view=view, ephemeral=ephemeral)
                    else:
                        logger.info(f"Sending followup with content only and view={view is not None}")
                        await interaction.followup.send(content=content, view=view, ephemeral=ephemeral)
                    logger.info("Successfully sent followup message")
                except discord.HTTPException as e:
                    logger.error(f"Discord HTTPException in followup.send: {e}")
                    logger.error(f"Content length: {len(content)}, Embeds: {len(embeds)}, View: {view is not None}")
                except discord.Forbidden as e:
                    logger.error(f"Discord Forbidden in followup.send: {e}")
                except discord.NotFound as e:
                    logger.error(f"Discord NotFound in followup.send: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error in followup.send: {e}")
                    logger.error(f"Error type: {type(e)}")
            
            elif response_type == 5:  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
                logger.info("Processing DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE - already deferred")
                pass
            
            elif response_type == 6:  # DEFERRED_UPDATE_MESSAGE
                logger.info("Processing DEFERRED_UPDATE_MESSAGE - already deferred")
                pass
            
            elif response_type == 7:  # UPDATE_MESSAGE
                logger.info("Processing UPDATE_MESSAGE response")
                data = lambda_response.get('data', {})
                content = data.get('content', '')
                logger.info(f"Update content: {content}")
                
                embeds = []
                if 'embeds' in data:
                    logger.info(f"Processing {len(data['embeds'])} embeds for update")
                    for i, embed_data in enumerate(data['embeds']):
                        try:
                            embed = discord.Embed(
                                title=embed_data.get('title'),
                                description=embed_data.get('description'),
                                color=embed_data.get('color', 0x0099ff)
                            )
                            embeds.append(embed)
                            logger.info(f"Successfully created update embed {i}")
                        except Exception as e:
                            logger.error(f"Error creating update embed {i}: {e}")
                
                view = None
                if 'components' in data:
                    logger.info("Processing components for update")
                    try:
                        view = self.create_view_from_components(data['components'])
                        logger.info("Successfully created view for update")
                    except Exception as e:
                        logger.error(f"Error creating view for update: {e}")
                
                try:
                    if embeds:
                        logger.info("Editing original response with embeds")
                        await interaction.edit_original_response(content=content, embeds=embeds, view=view)
                    else:
                        logger.info("Editing original response without embeds")
                        await interaction.edit_original_response(content=content, view=view)
                    logger.info("Successfully updated original message")
                except Exception as e:
                    logger.error(f"Error editing original response: {e}")
            
            else:
                logger.warning(f"Unknown response type: {response_type}")
                logger.warning(f"Full response: {lambda_response}")
            
            logger.info(f"Successfully handled interaction for {interaction.user}")
            
        except TimeoutError:
            if not interaction.response.is_done():
                await interaction.response.defer()
            embed = discord.Embed(
                title="⏰ Timeout",
                description="Request timed out. Please try again!",
                color=0xff9900
            )
            try:
                await interaction.followup.send(embed=embed, ephemeral=True)
            except:
                logger.error("Failed to send timeout message")
            
        except Exception as e:
            logger.error(f"Error handling interaction: {e}")
            if not interaction.response.is_done():
                await interaction.response.defer()
            embed = discord.Embed(
                title="❌ Error",
                description="Something went wrong. Please try again later!",
                color=0xff4444
            )
            try:
                await interaction.followup.send(embed=embed, ephemeral=True)
            except:
                logger.error("Failed to send error message")

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
