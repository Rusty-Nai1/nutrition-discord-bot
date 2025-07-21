import discord
from discord.ext import commands
import requests
import json
import asyncio
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
LAMBDA_ENDPOINT = os.getenv('LAMBDA_ENDPOINT')

if not DISCORD_TOKEN or not LAMBDA_ENDPOINT:
    raise ValueError("Missing required environment variables")

class SimpleHandler:
    def serialize_interaction(self, interaction: discord.Interaction) -> dict:
        """Convert Discord interaction to Lambda format"""
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
        """Call Lambda endpoint"""
        try:
            response = requests.post(
                LAMBDA_ENDPOINT,
                json=interaction_data,
                headers={"Content-Type": "application/json"},
                timeout=20
            )
            
            if response.status_code == 200:
                data = response.json()
                return json.loads(data['body']) if 'body' in data else data
            else:
                logger.error(f"Lambda error: {response.status_code}")
                raise Exception(f"Lambda returned {response.status_code}")
                
        except Exception as e:
            logger.error(f"Lambda call failed: {e}")
            raise
    
    def create_buttons(self, components: list) -> discord.ui.View:
        """Create Discord view from components"""
        view = discord.ui.View(timeout=300)
        
        for component_row in components:
            if component_row.get('type') == 1:  # Action Row
                for component in component_row.get('components', []):
                    if component.get('type') == 2:  # Button
                        button = discord.ui.Button(
                            style=discord.ButtonStyle.primary,
                            label=component.get('label', ''),
                            custom_id=component.get('custom_id')
                        )
                        
                        # Fix closure issue with proper binding
                        def make_callback(handler):
                            async def button_callback(interaction: discord.Interaction):
                                await handler.handle_interaction(interaction)
                            return button_callback
                        
                        button.callback = make_callback(self)
                        view.add_item(button)
        
        return view
    
    def create_modal(self, modal_data: dict) -> discord.ui.Modal:
        """Create Discord modal from data"""
        class DynamicModal(discord.ui.Modal):
            def __init__(self, modal_data, handler):
                super().__init__(title=modal_data.get('title', 'Form'))
                self.handler = handler
                
                for component_row in modal_data.get('components', []):
                    if component_row.get('type') == 1:  # Action Row
                        for component in component_row.get('components', []):
                            if component.get('type') == 4:  # Text Input
                                text_input = discord.ui.TextInput(
                                    label=component.get('label', ''),
                                    placeholder=component.get('placeholder', ''),
                                    required=component.get('required', True),
                                    max_length=component.get('max_length', 1000)
                                )
                                text_input.custom_id = component.get('custom_id', '')
                                self.add_item(text_input)
            
            async def on_submit(self, interaction: discord.Interaction):
                await self.handler.handle_interaction(interaction)
        
        return DynamicModal(modal_data, self)
    
    async def handle_interaction(self, interaction: discord.Interaction):
        """Main interaction handler"""
        try:
            logger.info(f"Handling interaction {interaction.id} type {interaction.type}")
            
            # Serialize and send to Lambda
            interaction_data = self.serialize_interaction(interaction)
            lambda_response = await self.call_lambda(interaction_data)
            
            response_type = lambda_response.get('type', 4)
            logger.info(f"Lambda response type: {response_type}")
            
            # Handle modal response (type 9) - MUST respond immediately
            if response_type == 9:
                modal_data = lambda_response.get('data', {})
                modal = self.create_modal(modal_data)
                await interaction.response.send_modal(modal)
                logger.info("Modal sent successfully")
                return
            
            # Handle message with buttons (type 4)
            if response_type == 4:
                data = lambda_response.get('data', {})
                content = data.get('content', '')
                
                # Create embeds if present
                embeds = []
                if 'embeds' in data:
                    for embed_data in data['embeds']:
                        embed = discord.Embed(
                            title=embed_data.get('title'),
                            description=embed_data.get('description'),
                            color=embed_data.get('color', 0x0099ff)
                        )
                        embeds.append(embed)
                
                # Create buttons if present
                view = None
                if 'components' in data:
                    view = self.create_buttons(data['components'])
                
                # Send response
                ephemeral = data.get('flags', 0) & 64 == 64
                
                if embeds:
                    await interaction.response.send_message(
                        content=content, 
                        embeds=embeds, 
                        view=view, 
                        ephemeral=ephemeral
                    )
                else:
                    await interaction.response.send_message(
                        content=content, 
                        view=view, 
                        ephemeral=ephemeral
                    )
                
                logger.info("Message sent successfully")
                return
            
            # Handle other response types
            logger.warning(f"Unhandled response type: {response_type}")
            
        except Exception as e:
            logger.error(f"Error handling interaction: {e}")
            
            # Send error message if we haven't responded yet
            if not interaction.response.is_done():
                try:
                    await interaction.response.send_message(
                        "❌ Something went wrong. Please try again!", 
                        ephemeral=True
                    )
                except:
                    pass

handler = SimpleHandler()

@bot.event
async def on_ready():
    logger.info(f'{bot.user} connected!')
    logger.info(f'Bot in {len(bot.guilds)} servers')
    
    try:
        synced = await bot.tree.sync()
        logger.info(f'Synced {len(synced)} commands')
    except Exception as e:
        logger.error(f'Command sync failed: {e}')

@bot.tree.command(name="hi", description="Start conversation in English")
async def hi(interaction: discord.Interaction):
    await handler.handle_interaction(interaction)

@bot.tree.command(name="hola", description="Iniciar conversación en español") 
async def hola(interaction: discord.Interaction):
    await handler.handle_interaction(interaction)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
