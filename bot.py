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

class NutritionView(discord.ui.View):
    def __init__(self, language='en'):
        super().__init__(timeout=300)
        self.language = language
        
    @discord.ui.button(label='🥗 Meal Planning', style=discord.ButtonStyle.primary)
    async def meal_planning(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'meal_planning')
    
    @discord.ui.button(label='💪 Fitness Goals', style=discord.ButtonStyle.primary)
    async def fitness_goals(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'fitness_goals')
    
    @discord.ui.button(label='🔍 Food Analysis', style=discord.ButtonStyle.primary)
    async def food_analysis(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'food_analysis')
    
    @discord.ui.button(label='❓ General Questions', style=discord.ButtonStyle.secondary)
    async def general_questions(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_category(interaction, 'general_questions')
    
    async def handle_category(self, interaction: discord.Interaction, category: str):
        try:
            await interaction.response.defer(thinking=True)
            
            # Send to Lambda for processing
            payload = {
                'type': 'category_selection',
                'category': category,
                'language': self.language,
                'user_id': str(interaction.user.id),
                'channel_id': str(interaction.channel.id)
            }
            
            response = await send_to_lambda(payload)
            
            if response and response.get('type') == 9:  # MODAL response
                modal_data = response.get('data', {})
                modal = NutritionModal(
                    title=modal_data.get('title', 'Nutrition Form'),
                    category=category,
                    language=self.language
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
    def __init__(self, title: str, category: str, language: str):
        super().__init__(title=title)
        self.category = category
        self.language = language
        
        # Add text inputs based on category
        if category == 'meal_planning':
            self.add_item(discord.ui.TextInput(
                label='Dietary Preferences',
                placeholder='e.g., vegetarian, gluten-free, low-carb...',
                max_length=500,
                required=False
            ))
            self.add_item(discord.ui.TextInput(
                label='Goals & Restrictions',
                placeholder='e.g., weight loss, muscle gain, allergies...',
                style=discord.TextStyle.paragraph,
                max_length=1000,
                required=False
            ))
        elif category == 'fitness_goals':
            self.add_item(discord.ui.TextInput(
                label='Current Fitness Level',
                placeholder='e.g., beginner, intermediate, advanced...',
                max_length=300,
                required=False
            ))
            self.add_item(discord.ui.TextInput(
                label='Goals & Timeline',
                placeholder='e.g., lose 10 lbs in 3 months, gain muscle...',
                style=discord.TextStyle.paragraph,
                max_length=1000,
                required=False
            ))
        elif category == 'food_analysis':
            self.add_item(discord.ui.TextInput(
                label='Food/Meal to Analyze',
                placeholder='e.g., chicken caesar salad, protein shake...',
                max_length=500,
                required=True
            ))
            self.add_item(discord.ui.TextInput(
                label='Specific Questions',
                placeholder='e.g., calories, nutrients, healthiness...',
                style=discord.TextStyle.paragraph,
                max_length=1000,
                required=False
            ))
        else:  # general_questions
            self.add_item(discord.ui.TextInput(
                label='Your Question',
                placeholder='Ask anything about nutrition, diet, or health...',
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

async def send_to_lambda(payload):
    """Send request to Lambda endpoint
