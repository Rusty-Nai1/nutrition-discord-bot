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
        
        labels = button_labels.get(language, button_labels['EN'])
        
        # Clear default items and add language-specific buttons
        self.clear_items()
        
        self.add_item(discord.ui.Button(
            label=labels['recipes'], 
            style=discord.ButtonStyle.primary, 
            custom_id='category_recipes',
            callback=self.recipes_callback
        ))
        self.add_item(discord.ui.Button(
            label=labels['nutrition'], 
            style=discord.ButtonStyle.primary, 
            custom_id='category_nutrition',
            callback=self.nutrition_callback
        ))
        self.add_item(discord.ui.Button(
            label=labels['mealprep'], 
            style=discord.ButtonStyle.primary, 
            custom_id='category_mealprep',
            callback=self.mealprep_callback
        ))
        self.add_item(discord.ui.Button(
            label=labels['workout'], 
            style=discord.ButtonStyle.secondary, 
            custom_id='category_workout',
            callback=self.workout_callback
        ))
    
    async def recipes_callback(self, interaction):
        await self.handle_category(interaction, 'recipes')
    
    async def nutrition_callback(self, interaction):
        await self.handle_category(interaction, 'nutrition')
    
    async def mealprep_callback(self, interaction):
        await self.handle_category(interaction, 'mealprep')
    
    async def workout_callback(self, interaction):
        await self.handle_category(interaction, 'workout')
    
    async def handle_category(self, interaction: discord.Interaction, category: str):
        try:
            await interaction.response.defer(thinking=True)
            
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
                modal = NutritionModal(
                    title=modal_data.get('title', 'Nutrition Form')[:45],  # Discord limit
                    category=category,
                    language=self.language,
                    modal_data=modal_data
                )
                await interaction.followup.send_modal(modal)
            else:
                # Standard message response
                content = response.get('content', 'Processing your request...')
                await interaction.followup.send(content)
                
        except Exception as e:
            logger.error(f"Error handling category {category}: {e}")
            await interaction.followup.send("Sorry, there was an error processing your request.")

class NutritionModal(discord.ui.Modal):
    def __init__(self, title: str, category: str, language: str, modal_data: dict = None):
        super().__init__(title=title)
        self.category = category
        self.language = language
        
        # Use modal_data from Lambda to build form fields
        if modal_data and 'components' in modal_data:
            for component_row in modal_data['components']:
                if component_row.get('type') == 1:  # Action Row
                    for component in component_row.get('components', []):
                        if component.get('type') == 4:  # Text Input
                            text_input = discord.ui.TextInput(
                                label=component.get('label', 'Input')[:45],
                                placeholder=component.get('placeholder', ''),
                                style=discord.TextStyle.paragraph if component.get('style') == 2 else discord.TextStyle.short,
                                max_length=component.get('max_length', 1000),
                                required=component.get('required', False)
                            )
                            text_input.custom_id = component.get('custom_id', 'field')
                            self.add_item(text_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True)
            
            # Build components structure matching Lambda's expected format
            components = []
            for item in self.children:
                if isinstance(item, discord.ui.TextInput):
                    components.append({
                        'type': 1,
                        'components': [{
                            'type': 4,
                            'custom_id': getattr(item, 'custom_id', 'field'),
                            'value': item.value
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
            
            response = await send_to_lambda(payload)
            
            if response:
                content = response.get('content', 'Thank you for your submission!')
                await interaction.followup.send(content)
            else:
                await interaction.followup.send("Thank you for your submission! Processing your request...")
                
        except Exception as e:
            logger.error(f"Error submitting form: {e}")
            await interaction.followup.send("Sorry, there was an error processing your submission.")

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
        embed = discord.Embed(
            title="🍎 Nutrition Assistant",
            description="Choose what you'd like help with:",
            color=discord.Color.green()
        )
        view = NutritionView('EN')
        await interaction.response.send_message(embed=embed, view=view)
    except Exception as e:
        logger.error(f"Error in hi command: {e}")
        await interaction.response.send_message("❌ Error occurred", ephemeral=True)

@bot.tree.command(name="hola", description="Iniciar análisis nutricional en español")
async def hola_command(interaction: discord.Interaction):
    try:
        embed = discord.Embed(
            title="🍎 Asistente de Nutrición",
            description="Elige con qué te gustaría ayuda:",
            color=discord.Color.green()
        )
        view = NutritionView('ES')
        await interaction.response.send_message(embed=embed, view=view)
    except Exception as e:
        logger.error(f"Error in hola command: {e}")
        await interaction.response.send_message("❌ Error occurred", ephemeral=True)

@bot.tree.command(name="salut", description="Commencer l'analyse nutritionnelle en français")
async def salut_command(interaction: discord.Interaction):
    try:
        embed = discord.Embed(
            title="🍎 Assistant Nutritionnel",
            description="Choisissez ce avec quoi vous aimeriez de l'aide:",
            color=discord.Color.green()
        )
        view = NutritionView('FR')
        await interaction.response.send_message(embed=embed, view=view)
    except Exception as e:
        logger.error(f"Error in salut command: {e}")
        await interaction.response.send_message("❌ Error occurred", ephemeral=True)

@bot.tree.command(name="jambo", description="Anza uchambuzi wa lishe kwa Kiswahili")
async def jambo_command(interaction: discord.Interaction):
    try:
        embed = discord.Embed(
            title="🍎 Msaidizi wa Lishe",
            description="Chagua unachotaka msaada nao:",
            color=discord.Color.green()
        )
        view = NutritionView('SW')
        await interaction.response.send_message(embed=embed, view=view)
    except Exception as e:
        logger.error(f"Error in jambo command: {e}")
        await interaction.response.send_message("❌ Error occurred", ephemeral=True)

@bot.tree.command(name="muraho", description="Tangira isesengura ry'intungamubiri mu Kinyarwanda")
async def muraho_command(interaction: discord.Interaction):
    try:
        embed = discord.Embed(
            title="🍎 Umufasha w'Intungamubiri",
            description="Hitamo icyo ushaka ubufasha:",
            color=discord.Color.green()
        )
        view = NutritionView('RW')
        await interaction.response.send_message(embed=embed, view=view)
    except Exception as e:
        logger.error(f"Error in muraho command: {e}")
        await interaction.response.send_message("❌ Error occurred", ephemeral=True)

if __name__ == '__main__':
    bot.run(DISCORD_TOKEN)
