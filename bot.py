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
        
    @discord.ui.button(label='🥗 Recipes', style=discord.ButtonStyle.primary, custom_id='category_recipes')
    async def recipes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'recipes')
    
    @discord.ui.button(label='📊 Nutrition', style=discord.ButtonStyle.primary, custom_id='category_nutrition')
    async def nutrition(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'nutrition')
    
    @discord.ui.button(label='🍽️ Meal Prep', style=discord.ButtonStyle.primary, custom_id='category_mealprep')
    async def mealprep(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'mealprep')
    
    @discord.ui.button(label='💪 Workout', style=discord.ButtonStyle.secondary, custom_id='category_workout')
    async def workout(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        
        # Use modal_data from Lambda if provided, otherwise fallback
        if modal_data and 'components' in modal_data:
            for component in modal_data['components']:
                if component.get('type') == 4:  # Text Input
                    self.add_item(discord.ui.TextInput(
                        label=component.get('label', 'Input')[:45],
                        placeholder=component.get('placeholder', ''),
                        style=discord.TextStyle.paragraph if component.get('style') == 2 else discord.TextStyle.short,
                        max_length=component.get('max_length', 1000),
                        required=component.get('required', False)
                    ))
        else:
            # Fallback to original structure
            if category == 'mealprep':
                self.add_item(discord.ui.TextInput(
                    label='Dietary Preferences'[:45],
                    placeholder='e.g., vegetarian, gluten-free, low-carb...',
                    max_length=500,
                    required=False
                ))
                self.add_item(discord.ui.TextInput(
                    label='Goals & Restrictions'[:45],
                    placeholder='e.g., weight loss, muscle gain, allergies...',
                    style=discord.TextStyle.paragraph,
                    max_length=1000,
                    required=False
                ))
            elif category == 'workout':
                self.add_item(discord.ui.TextInput(
                    label='Current Fitness Level'[:45],
                    placeholder='e.g., beginner, intermediate, advanced...',
                    max_length=300,
                    required=False
                ))
                self.add_item(discord.ui.TextInput(
                    label='Goals & Timeline'[:45],
                    placeholder='e.g., lose 10 lbs in 3 months, gain muscle...',
                    style=discord.TextStyle.paragraph,
                    max_length=1000,
                    required=False
                ))
            elif category == 'nutrition':
                self.add_item(discord.ui.TextInput(
                    label='Food/Meal to Analyze'[:45],
                    placeholder='e.g., chicken caesar salad, protein shake...',
                    max_length=500,
                    required=True
                ))
                self.add_item(discord.ui.TextInput(
                    label='Specific Questions'[:45],
                    placeholder='e.g., calories, nutrients, healthiness...',
                    style=discord.TextStyle.paragraph,
                    max_length=1000,
                    required=False
                ))
            else:  # recipes
                self.add_item(discord.ui.TextInput(
                    label='Recipe Request'[:45],
                    placeholder='e.g., high protein breakfast, vegan dinner...',
                    style=discord.TextStyle.paragraph,
                    max_length=2000,
                    required=True
                ))
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(thinking=True)
            
            # Collect form data
            form_data = {}
            for item in self.children:
                if isinstance(item, discord.ui.TextInput):
                    form_data[item.label.lower().replace(' ', '_')] = item.value
            
            # Send to Lambda for processing
            payload = {
                'type': 'form_submission',
                'category': self.category,
                'language': self.language,
                'form_data': form_data,
                'user_id': str(interaction.user.id),
                'channel_id': str(interaction.channel.id)
            }
            
            response = await send_to_lambda(payload)
            
            if response:
                content = response.get('content', 'Thank you for your submission!')
                embeds = response.get('embeds', [])
                
                if embeds:
                    embed_objects = []
                    for embed_data in embeds:
                        embed = discord.Embed(
                            title=embed_data.get('title'),
                            description=embed_data.get('description'),
                            color=discord.Color.green()
                        )
                        embed_objects.append(embed)
                    await interaction.followup.send(content=content, embeds=embed_objects)
                else:
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
        logger.info(f'Synced {len(synced)} slash commands')
        
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
