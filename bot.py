import discord
from discord.ext import commands
import aiohttp
import asyncio
import logging
import os
import json

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Environment variables
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
LAMBDA_ENDPOINT = os.getenv('LAMBDA_ENDPOINT')

if not DISCORD_TOKEN or not LAMBDA_ENDPOINT:
    logger.error("Missing required environment variables: DISCORD_TOKEN or LAMBDA_ENDPOINT")
    exit(1)

# Global Lambda client
async def send_to_lambda(payload):
    """Send request to Lambda endpoint"""
    headers = {"Content-Type": "application/json"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                LAMBDA_ENDPOINT,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'body' in data:
                        return json.loads(data['body'])
                    return data
                else:
                    logger.error(f"Lambda returned status {response.status}: {await response.text()}")
                    return None
                    
    except Exception as e:
        logger.error(f"Lambda request failed: {e}")
        return None

class NutritionView(discord.ui.View):
    def __init__(self, language='EN'):
        super().__init__(timeout=300)
        self.language = language
        
        # Update button labels based on language
        button_labels = {
            'EN': {'recipes': '🥗 Recipes', 'nutrition': '📊 Nutrition', 'mealprep': '🍽️ Meal Prep', 'workout': '💪 Workout'},
            'ES': {'recipes': '🥗 Recetas', 'nutrition': '📊 Nutrición', 'mealprep': '🍽️ Preparación', 'workout': '💪 Ejercicio'},
            'FR': {'recipes': '🥗 Recettes', 'nutrition': '📊 Nutrition', 'mealprep': '🍽️ Préparation', 'workout': '💪 Exercice'},
            'SW': {'recipes': '🥗 Mapishi', 'nutrition': '📊 Lishe', 'mealprep': '🍽️ Kuandaa', 'workout': '💪 Mazoezi'},
            'RW': {'recipes': '🥗 Guteka', 'nutrition': '📊 Indyo', 'mealprep': '🍽️ Gutegura', 'workout': '💪 Imyitozo'}
        }
        
        self.labels = button_labels.get(language, button_labels['EN'])
        
        # Update button labels after initialization
        self._update_button_labels()
    
    def _update_button_labels(self):
        # Update labels after buttons are created
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == 'category_recipes':
                    item.label = self.labels['recipes']
                elif item.custom_id == 'category_nutrition':
                    item.label = self.labels['nutrition']
                elif item.custom_id == 'category_mealprep':
                    item.label = self.labels['mealprep']
                elif item.custom_id == 'category_workout':
                    item.label = self.labels['workout']
    
    @discord.ui.button(label='Placeholder', style=discord.ButtonStyle.primary, custom_id='category_recipes')
    async def recipes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'recipes')
    
    @discord.ui.button(label='Placeholder', style=discord.ButtonStyle.primary, custom_id='category_nutrition')
    async def nutrition_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'nutrition')
    
    @discord.ui.button(label='Placeholder', style=discord.ButtonStyle.primary, custom_id='category_mealprep')
    async def mealprep_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'mealprep')
    
    @discord.ui.button(label='Placeholder', style=discord.ButtonStyle.secondary, custom_id='category_workout')
    async def workout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'workout')
    
    async def on_timeout(self):
        pass
    
    async def handle_category(self, interaction: discord.Interaction, category: str):
        try:
            # DON'T defer - modals must be initial response
            
            # Create Discord interaction format your Lambda expects
            payload = {
                "type": 3,
                "data": {
                    "custom_id": f"category_{category}_{self.language}",
                    "component_type": 2
                },
                "user": {
                    "id": str(interaction.user.id)
                },
                "channel_id": str(interaction.channel.id)
            }
            
            response = await send_to_lambda(payload)
            
            if response and response.get('type') == 9:  # MODAL response
                modal_data = response.get('data', {})
                title = modal_data.get('title', 'Form')[:45]
                logger.info(f"Creating modal - Title: '{title}' (length: {len(title)})")
                logger.info(f"Modal components count: {len(modal_data.get('components', []))}")
                
                modal = NutritionModal(
                    title=title,
                    category=category,
                    language=self.language,
                    modal_data=modal_data
                )
                # Send modal as INITIAL response (not followup)
                await interaction.response.send_modal(modal)
            else:
                content = response.get('content', 'Processing...') if response else 'Error occurred'
                await interaction.response.send_message(content)
                
        except Exception as e:
            logger.error(f"Error handling category {category}: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("Error processing request.", ephemeral=True)
                else:
                    await interaction.followup.send("Error processing request.", ephemeral=True)
            except:
                pass

class NutritionModal(discord.ui.Modal):
    def __init__(self, title: str, category: str, language: str, modal_data: dict = None):
        super().__init__(title=title)
        self.category = category
        self.language = language
        
        logger.info(f"Building modal for {language} - {category}")
        
        # Use modal_data from Lambda to build form fields
        if modal_data and 'components' in modal_data:
            component_count = 0
            for component_row in modal_data['components']:
                if component_row.get('type') == 1 and component_count < 5:  # Action Row, max 5 components
                    for component in component_row.get('components', []):
                        if component.get('type') == 4:  # Text Input
                            try:
                                label = component.get('label', 'Input')[:45]  # Discord 45 char limit
                                placeholder = component.get('placeholder', '')[:100]  # Discord 100 char limit
                                custom_id = component.get('custom_id', f'field_{component_count}')[:100]
                                
                                logger.info(f"Adding field - Label: '{label}' ({len(label)} chars), ID: '{custom_id}'")
                                
                                text_input = discord.ui.TextInput(
                                    label=label,
                                    placeholder=placeholder,
                                    style=discord.TextStyle.paragraph if component.get('style') == 2 else discord.TextStyle.short,
                                    max_length=min(component.get('max_length', 1000), 4000),  # Discord max
                                    required=component.get('required', False)
                                )
                                text_input.custom_id = custom_id
                                self.add_item(text_input)
                                component_count += 1
                                
                                if component_count >= 5:  # Discord modal limit
                                    break
                                    
                            except Exception as e:
                                logger.error(f"Error adding modal field: {e}")
                                continue
        
        logger.info(f"Modal created with {len(self.children)} fields")
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            logger.info(f"Modal submitted for {self.language} - {self.category}")
            await interaction.response.defer(thinking=True)
            
            # Build components structure matching Lambda's expected format
            components = []
            for item in self.children:
                if isinstance(item, discord.ui.TextInput):
                    field_id = getattr(item, 'custom_id', 'field')
                    field_value = item.value or ''
                    logger.info(f"Field {field_id}: '{field_value}' ({len(field_value)} chars)")
                    
                    components.append({
                        'type': 1,
                        'components': [{
                            'type': 4,
                            'custom_id': field_id,
                            'value': field_value
                        }]
                    })
            
            # Create Discord modal submit format
            custom_id = f"nutrition_modal_{self.category}_{self.language}" if self.category != 'workout' else f"workout_modal_{self.language}"
            
            payload = {
                "type": 5,  # MODAL_SUBMIT
                "data": {
                    "custom_id": custom_id,
                    "components": components
                },
                "user": {
                    "id": str(interaction.user.id)
                },
                "channel_id": str(interaction.channel.id)
            }
            
            logger.info(f"Sending payload to Lambda: {custom_id}")
            response = await send_to_lambda(payload)
            
            # DEBUG: Log the actual response
            logger.info(f"Lambda response: {json.dumps(response, indent=2) if response else 'None'}")
            
            if response:
                # Try multiple response formats
                content = None
                if 'data' in response and 'content' in response['data']:
                    content = response['data']['content']
                elif 'content' in response:
                    content = response['content']
                elif 'body' in response:
                    try:
                        body_data = json.loads(response['body']) if isinstance(response['body'], str) else response['body']
                        if 'data' in body_data and 'content' in body_data['data']:
                            content = body_data['data']['content']
                    except:
                        pass
                
                if not content:
                    content = 'Thank you for your submission! Processing your request...'
                
                logger.info(f"Extracted content: {content[:100]}...")
                await interaction.followup.send(content)
            else:
                logger.warning("No response from Lambda")
                await interaction.followup.send("Thank you for your submission! Processing your request...")
                
        except Exception as e:
            logger.error(f"Error submitting form for {self.language}-{self.category}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            try:
                await interaction.followup.send("Sorry, there was an error processing your submission.")
            except:
                pass

@bot.event
async def on_ready():
    try:
        logger.info(f'{bot.user} has connected to Discord!')
        logger.info(f'Bot is in {len(bot.guilds)} guilds')
        
        # Sync commands
        synced = await bot.tree.sync()
        logger.info(f'Synced {len(synced)} slash commands: {[cmd.name for cmd in synced]}')
        
    except Exception as e:
        logger.error(f'Error in on_ready: {e}')

@bot.tree.command(name="hi", description="Start nutrition analysis in English")
async def hi_command(interaction: discord.Interaction):
    try:
        if interaction.response.is_done():
            return
        
        embed = discord.Embed(
            title="🍎 Nutrition Assistant",
            description="Choose what you'd like help with:",
            color=discord.Color.green()
        )
        view = NutritionView('EN')
        await interaction.response.send_message(embed=embed, view=view)
    except discord.errors.NotFound:
        pass  # Interaction expired
    except Exception as e:
        logger.error(f"Error in hi command: {e}")
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message("❌ Error occurred", ephemeral=True)
            except discord.errors.NotFound:
                pass

@bot.tree.command(name="hola", description="Iniciar análisis nutricional en español")
async def hola_command(interaction: discord.Interaction):
    try:
        if interaction.response.is_done():
            return
        
        embed = discord.Embed(
            title="🍎 Asistente de Nutrición",
            description="Elige con qué te gustaría ayuda:",
            color=discord.Color.green()
        )
        view = NutritionView('ES')
        await interaction.response.send_message(embed=embed, view=view)
    except discord.errors.NotFound:
        pass  # Interaction expired
    except Exception as e:
        logger.error(f"Error in hola command: {e}")
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message("❌ Error occurred", ephemeral=True)
            except discord.errors.NotFound:
                pass

@bot.tree.command(name="salut", description="Commencer l'analyse nutritionnelle en français")
async def salut_command(interaction: discord.Interaction):
    try:
        if interaction.response.is_done():
            return
        
        embed = discord.Embed(
            title="🍎 Assistant Nutritionnel",
            description="Choisissez ce avec quoi vous aimeriez de l'aide:",
            color=discord.Color.green()
        )
        view = NutritionView('FR')
        await interaction.response.send_message(embed=embed, view=view)
    except discord.errors.NotFound:
        pass  # Interaction expired
    except Exception as e:
        logger.error(f"Error in salut command: {e}")
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message("❌ Error occurred", ephemeral=True)
            except discord.errors.NotFound:
                pass

@bot.tree.command(name="jambo", description="Anza uchambuzi wa lishe kwa Kiswahili")
async def jambo_command(interaction: discord.Interaction):
    try:
        if interaction.response.is_done():
            return
        
        embed = discord.Embed(
            title="🍎 Msaidizi wa Lishe",
            description="Chagua unachotaka msaada nao:",
            color=discord.Color.green()
        )
        view = NutritionView('SW')
        await interaction.response.send_message(embed=embed, view=view)
    except discord.errors.NotFound:
        pass  # Interaction expired
    except Exception as e:
        logger.error(f"Error in jambo command: {e}")
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message("❌ Error occurred", ephemeral=True)
            except discord.errors.NotFound:
                pass

@bot.tree.command(name="muraho", description="Tangira isesengura ry'intungamubiri mu Kinyarwanda")
async def muraho_command(interaction: discord.Interaction):
    try:
        if interaction.response.is_done():
            return
        
        embed = discord.Embed(
            title="🍎 Umufasha w'Intungamubiri",
            description="Hitamo icyo ushaka ubufasha:",
            color=discord.Color.green()
        )
        view = NutritionView('RW')
        await interaction.response.send_message(embed=embed, view=view)
    except discord.errors.NotFound:
        pass  # Interaction expired
    except Exception as e:
        logger.error(f"Error in muraho command: {e}")
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message("❌ Error occurred", ephemeral=True)
            except discord.errors.NotFound:
                pass

if __name__ == '__main__':
    bot.run(DISCORD_TOKEN)
