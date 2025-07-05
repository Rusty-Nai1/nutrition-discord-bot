# Nutrition Discord Bot

A Discord bot that provides personalized nutrition advice for Amazon FC workers using Claude AI through AWS Lambda.

## Features

- **4 Slash Commands**: `/preshift`, `/break`, `/recovery`, `/meal_prep`
- **AI-Powered**: Connects to Claude AI via AWS Lambda for personalized advice
- **Professional UI**: Styled Discord embeds with color-coded responses
- **Error Handling**: Robust error handling with user-friendly messages
- **Production Ready**: Deployed on Railway with monitoring and logging

## Commands

- `/preshift [details]` - Get nutrition advice before your shift
- `/break [details]` - Get nutrition suggestions for your break  
- `/recovery [details]` - Get post-shift recovery nutrition advice
- `/meal_prep [details]` - Get meal preparation planning advice
- `/help` - Show all available commands

## Setup & Deployment

### Prerequisites
- Discord Bot Token
- Lambda API endpoint (already configured)
- Railway account

### Environment Variables
Set these in Railway: