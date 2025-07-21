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
       self.api_timeout = 25
       self.max_retries = 2
   
   def is_interaction_valid(self, interaction: discord.Interaction) -> bool:
       try:
           if interaction.response.is_done():
               return True
           return True
       except Exception as e:
           logger.warning(f"Error checking interaction validity: {e}")
           return False
   
   def serialize_interaction(self, interaction: discord.Interaction) -> dict:
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
                   logger.error(f"Lambda returned status {response.status_code}: {response.text}")
                   raise requests.exceptions.RequestException(f"Lambda error: {response.status_code}")
                   
           except requests.exceptions.Timeout:
               logger.warning(f"Lambda timeout on attempt {attempt + 1}")
               if attempt == self.max_retries:
                   raise TimeoutError("Lambda request timed out")
               await asyncio.sleep(1)
               
           except requests.exceptions.RequestException as e:
               logger.error(f"Lambda request failed: {e}")
               if attempt == self.max_retries:
                   raise
               await asyncio.sleep(1)
   
   def create_view_from_components(self, components: list) -> discord.ui.View:
       view = discord.ui.View(timeout=300)
       
       for component_row in components:
           if component_row.get('type') == 1:
               for component in component_row.get('components', []):
                   if component.get('type') == 2:
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
                   if component_row.get('type') == 1:
                       for component in component_row.get('components', []):
                           if component.get('type') == 4:
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
   
   async def safe_defer(self, interaction: discord.Interaction, ephemeral: bool = False) -> bool:
       try:
           if not interaction.response.is_done():
               await interaction.response.defer(ephemeral=ephemeral)
               logger.info(f"Successfully deferred interaction {interaction.id}")
               return True
           else:
               logger.info(f"Interaction {interaction.id} already responded")
               return False
       except discord.errors.NotFound:
           logger.error(f"Interaction {interaction.id} token expired - cannot defer")
           return False
       except discord.errors.HTTPException as e:
           logger.error(f"HTTP error deferring interaction {interaction.id}: {e}")
           return False
       except Exception as e:
           logger.error(f"Unexpected error deferring interaction {interaction.id}: {e}")
           return False
   
   async def safe_send_response(self, interaction: discord.Interaction, content: str = None, 
                              embeds: list = None, view: discord.ui.View = None, 
                              ephemeral: bool = False) -> bool:
       try:
           if interaction.response.is_done():
               if embeds:
                   await interaction.followup.send(content=content, embeds=embeds, view=view, ephemeral=ephemeral)
               else:
                   await interaction.followup.send(content=content, view=view, ephemeral=ephemeral)
               logger.info(f"Sent followup for interaction {interaction.id}")
               return True
           else:
               if embeds:
                   await interaction.response.send_message(content=content, embeds=embeds, view=view, ephemeral=ephemeral)
               else:
                   await interaction.response.send_message(content=content, view=view, ephemeral=ephemeral)
               logger.info(f"Sent original response for interaction {interaction.id}")
               return True
       except discord.errors.NotFound:
           logger.error(f"Interaction {interaction.id} token expired - cannot send response")
           return False
       except discord.errors.HTTPException as e:
           logger.error(f"HTTP error sending response to interaction {interaction.id}: {e}")
           return False
       except Exception as e:
           logger.error(f"Unexpected error sending response to interaction {interaction.id}: {e}")
           return False
   
   async def handle_interaction(self, interaction: discord.Interaction):
       interaction_start_time = datetime.now()
       
       try:
           if not self.is_interaction_valid(interaction):
               logger.error(f"Invalid interaction {interaction.id}")
               return
           
           if interaction.type == discord.InteractionType.modal_submit:
               logger.info(f"Modal submission detected - deferring immediately")
               deferred = await self.safe_defer(interaction)
               if not deferred:
                   logger.error("Failed to defer modal submission")
                   return
           
           interaction_data = self.serialize_interaction(interaction)
           logger.info(f"Processing interaction {interaction.id} type {interaction.type}")
           
           try:
               lambda_response = await asyncio.wait_for(
                   self.call_lambda(interaction_data), 
                   timeout=self.api_timeout
               )
           except asyncio.TimeoutError:
               raise TimeoutError("Lambda request timed out")
           
           logger.info(f"Lambda response received for {interaction.id}")
           
           response_type = lambda_response.get('type', 4)
           logger.info(f"Processing response type: {response_type}")
           
           if response_type == 9:
               logger.info("Processing MODAL response")
               if interaction.response.is_done():
                   logger.error("Cannot send modal - interaction already responded")
                   return
               
               modal_data = lambda_response.get('data', {})
               try:
                   modal = self.create_modal_from_data(modal_data)
                   await interaction.response.send_modal(modal)
                   logger.info("Successfully sent modal")
                   return
               except discord.errors.NotFound:
                   logger.error("Modal send failed - interaction token expired")
                   return
               except Exception as e:
                   logger.error(f"Error creating/sending modal: {e}")
                   return
           
           if interaction.type != discord.InteractionType.modal_submit:
               deferred = await self.safe_defer(interaction)
           
           if response_type == 4:
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
               
               success = await self.safe_send_response(interaction, content, embeds, view, ephemeral)
               if not success:
                   logger.error("Failed to send message response")
           
           elif response_type == 5:
               logger.info("Processing DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE - already deferred")
               pass
           
           elif response_type == 6:
               logger.info("Processing DEFERRED_UPDATE_MESSAGE - already deferred")
               pass
           
           elif response_type == 7:
               logger.info("Processing UPDATE_MESSAGE response")
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
                           embeds.append(embed)
                       except Exception as e:
                           logger.error(f"Error creating update embed {i}: {e}")
               
               view = None
               if 'components' in data:
                   try:
                       view = self.create_view_from_components(data['components'])
                   except Exception as e:
                       logger.error(f"Error creating view for update: {e}")
               
               try:
                   if embeds:
                       await interaction.edit_original_response(content=content, embeds=embeds, view=view)
                   else:
                       await interaction.edit_original_response(content=content, view=view)
                   logger.info("Successfully updated original message")
               except discord.errors.NotFound:
                   logger.error("Cannot edit original response - interaction expired")
               except Exception as e:
                   logger.error(f"Error editing original response: {e}")
           
           else:
               logger.warning(f"Unknown response type: {response_type}")
           
           processing_time = (datetime.now() - interaction_start_time).total_seconds()
           logger.info(f"Interaction {interaction.id} processed in {processing_time:.2f}s")
           
       except TimeoutError:
           logger.error(f"Timeout handling interaction {interaction.id}")
           embed = discord.Embed(
               title="⏰ Timeout",
               description="Request timed out. Please try again!",
               color=0xff9900
           )
           await self.safe_send_response(interaction, embeds=[embed], ephemeral=True)
           
       except discord.errors.NotFound:
           logger.error(f"Interaction {interaction.id} token expired")
           
       except Exception as e:
           logger.error(f"Error handling interaction {interaction.id}: {e}")
           embed = discord.Embed(
               title="❌ Error",
               description="Something went wrong. Please try again later!",
               color=0xff4444
           )
           await self.safe_send_response(interaction, embeds=[embed], ephemeral=True)

handler = InteractionHandler()

@bot.event
async def on_ready():
   logger.info(f'{bot.user} connected to Discord!')
   
   # Wait for guild cache to populate
   await asyncio.sleep(3)
   
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

@bot.event
async def on_guild_available(guild):
   logger.info(f'Guild available: {guild.name} (ID: {guild.id})')
   logger.info(f'Bot is now in {len(bot.guilds)} servers')

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
