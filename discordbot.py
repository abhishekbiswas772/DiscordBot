import os
import json
import random
import asyncio
import discord
import requests
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import google.generativeai as genai
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CONFIG = {
    "DISCORD_TOKEN": os.getenv("DISCORD_TOKEN"),
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
    "REMINDER_CHANNEL_ID": int(os.getenv("REMINDER_CHANNEL_ID", 0)),  # Cast to int
    "STATUS_CHANNEL_ID": int(os.getenv("STATUS_CHANNEL_ID", 0)),      # Cast to int
    "JOB_CHANNEL_ID": int(os.getenv("JOB_CHANNEL_ID", 0)),            # Cast to int
    "DATA_DIR": "./data",
    "REMINDER_INTERVAL_HOURS": 3,
    # The PORT environment variable will be provided by Render
    "PORT": int(os.getenv("PORT", 10000)),
    # Render URL (update this with your actual app name when deployed)
    "RENDER_URL": os.getenv("RENDER_URL", "https://productivitypal.onrender.com"),
    # Render spins down after 15 minutes of inactivity
    "PING_INTERVAL_MINUTES": 14  # Ping every 14 minutes to stay active
}

# Create data directory if it doesn't exist
Path(CONFIG["DATA_DIR"]).mkdir(parents=True, exist_ok=True)

# Configure Gemini API
genai.configure(api_key=CONFIG["GEMINI_API_KEY"])

# Setup Discord bot with all intents
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command('help')  # Remove default help command to create custom one

# HTTP Server for health checks
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ProductivityPal Discord Bot is running!')
        
    def log_message(self, format, *args):
        # Suppress log messages to avoid cluttering the console
        return

# Function to run HTTP server
def run_http_server():
    server = HTTPServer(('0.0.0.0', CONFIG["PORT"]), SimpleHTTPRequestHandler)
    print(f"Starting HTTP server on port {CONFIG['PORT']}")
    server.serve_forever()

# Function to keep the service alive (prevent Render from spinning down)
def keep_alive():
    """Pings the bot's HTTP server to prevent Render from spinning down after 15 minutes of inactivity."""
    url = CONFIG["RENDER_URL"]
    while True:
        try:
            response = requests.get(url)
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{current_time}] Keep-alive ping sent. Status code: {response.status_code}")
        except Exception as e:
            print(f"Keep-alive error: {e}")
        # Sleep for 14 minutes (Render free tier timeout is 15 min)
        time.sleep(CONFIG["PING_INTERVAL_MINUTES"] * 60)

# Function to check channel access
async def check_channels():
    """Verify all required channels exist and are accessible."""
    missing_channels = []
    
    # Check reminder channel
    reminder_channel = bot.get_channel(CONFIG["REMINDER_CHANNEL_ID"])
    if not reminder_channel:
        missing_channels.append(("REMINDER_CHANNEL_ID", CONFIG["REMINDER_CHANNEL_ID"]))
    
    # Check status channel
    status_channel = bot.get_channel(CONFIG["STATUS_CHANNEL_ID"])
    if not status_channel:
        missing_channels.append(("STATUS_CHANNEL_ID", CONFIG["STATUS_CHANNEL_ID"]))
    
    # Check job channel
    job_channel = bot.get_channel(CONFIG["JOB_CHANNEL_ID"])
    if not job_channel:
        missing_channels.append(("JOB_CHANNEL_ID", CONFIG["JOB_CHANNEL_ID"]))
    
    # Report any missing channels
    if missing_channels:
        print("\n⚠️ CHANNEL ACCESS PROBLEMS DETECTED ⚠️")
        print("The bot cannot access the following channels:")
        for channel_name, channel_id in missing_channels:
            print(f"  - {channel_name}: {channel_id}")
        print("\nPossible solutions:")
        print("1. Verify the channel IDs are correct")
        print("2. Ensure the bot has been invited with correct permissions")
        print("3. Check if the channels exist in your Discord server")
        print("4. Make sure the bot has 'View Channel' permissions for these channels\n")
        return False
    else:
        print("All channels verified and accessible!")
        return True


class ReminderBot:
    def __init__(self, bot):
        self.bot = bot
        self.reminder_count = 0
        self.last_remind_time = None
        self.reminder_loop.start()
        self.load_state()
        
    def load_state(self):
        try:
            data_file = Path(CONFIG["DATA_DIR"]) / "reminder_state.json"
            if data_file.exists():
                with open(data_file, 'r') as f:
                    state = json.load(f)
                    self.reminder_count = state.get('reminder_count', 0)
                    self.last_remind_time = state.get('last_remind_time')
                print("Reminder Bot: State loaded successfully")
            else:
                print("Reminder Bot: No previous state found, starting fresh")
        except Exception as error:
            print(f"Reminder Bot: Error loading state: {error}")
    
    def save_state(self):
        try:
            data_file = Path(CONFIG["DATA_DIR"]) / "reminder_state.json"
            with open(data_file, 'w') as f:
                json.dump({
                    "reminder_count": self.reminder_count,
                    "last_remind_time": self.last_remind_time
                }, f)
        except Exception as error:
            print(f"Reminder Bot: Error saving state: {error}")
    
    def get_time_of_day(self):
        hour = datetime.now().hour
        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 21:
            return "evening"
        else:
            return "night"
    
    @tasks.loop(hours=CONFIG["REMINDER_INTERVAL_HOURS"])
    async def reminder_loop(self):
        await self.send_reminder()
    
    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()
        
    async def send_reminder(self):
        self.reminder_count += 1
        current_time = datetime.now()
        self.last_remind_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
        
        time_of_day = self.get_time_of_day()
        is_weekend = current_time.weekday() >= 5  # 5=Saturday, 6=Sunday
        
        channel = self.bot.get_channel(CONFIG["REMINDER_CHANNEL_ID"])
        if not channel:
            print(f"Reminder Bot: Could not find channel with ID {CONFIG['REMINDER_CHANNEL_ID']}")
            print("Reminder Bot: Skipping this reminder cycle.")
            return
        
        embed = discord.Embed(
            title=f"Productivity Reminder ({time_of_day})",
            description=f"Here's your {time_of_day} reminder to stay on track!",
            color=0x00ff00,
            timestamp=current_time
        )
        
        embed.add_field(name="🧩 DSA Questions", value="Complete 14 DSA questions", inline=False)
        if is_weekend:
            embed.add_field(name="💻 OS Concepts (Weekend Special)", value="Study 2 OS concepts", inline=False)
        embed.add_field(name="🚀 Personal Projects", value="Work on your personal projects", inline=False)
        embed.add_field(name="💼 Office Projects", value="Make progress on office assignments", inline=False)
        
        embed.set_footer(text=f"Reminder #{self.reminder_count}")
        
        await channel.send(embed=embed)
        self.save_state()


class ManagerBot:
    def __init__(self, bot):
        self.bot = bot
        self.conversations = []
        self.last_check_time = None
        self.status_checks = {}
        self.load_state()
        self.schedule_random_checks()
    
    def load_state(self):
        try:
            data_file = Path(CONFIG["DATA_DIR"]) / "manager_state.json"
            if data_file.exists():
                with open(data_file, 'r') as f:
                    state = json.load(f)
                    self.conversations = state.get('conversations', [])
                    self.last_check_time = state.get('last_check_time')
                print("Manager Bot: State loaded successfully")
            else:
                print("Manager Bot: No previous state found, starting fresh")
        except Exception as error:
            print(f"Manager Bot: Error loading state: {error}")
    
    def save_state(self):
        try:
            data_file = Path(CONFIG["DATA_DIR"]) / "manager_state.json"
            with open(data_file, 'w') as f:
                json.dump({
                    "conversations": self.conversations,
                    "last_check_time": self.last_check_time
                }, f)
        except Exception as error:
            print(f"Manager Bot: Error saving state: {error}")
    
    def schedule_random_checks(self):
        for task in self.status_checks.values():
            task.cancel()
        self.status_checks = {}
        current_time = datetime.now()
        for i in range(0, 24, CONFIG["REMINDER_INTERVAL_HOURS"]):
            random_minutes = random.randint(0, CONFIG["REMINDER_INTERVAL_HOURS"] * 60 - 1)
            check_time = current_time.replace(hour=i, minute=0, second=0) + timedelta(minutes=random_minutes)
            if check_time < current_time:
                check_time += timedelta(days=1)
            seconds_until_check = (check_time - current_time).total_seconds()
            task = asyncio.create_task(self.schedule_check(seconds_until_check))
            self.status_checks[i] = task
            print(f"Manager Bot: Check scheduled at {check_time.strftime('%H:%M')}")
    
    async def schedule_check(self, seconds_delay):
        await asyncio.sleep(seconds_delay)
        await self.check_status()
        await asyncio.sleep(24 * 60 * 60 - seconds_delay)
        self.schedule_random_checks()
    
    async def get_gemini_response(self, user_status):
        try:
            model = genai.GenerativeModel('gemini-pro')
            
            prompt = f"""
            Act as a supportive team leader and personal productivity coach. 
            The user has shared their current status: "{user_status}"
            
            Respond in 3-5 sentences with:
            1. Specific acknowledgment of their work
            2. Motivational encouragement that's genuine (not generic)
            3. One practical suggestion or question to help them improve or move forward
            4. Speak as a supportive leader (not a micromanaging boss)
            
            Keep your tone positive but authentic.
            """
            
            response = model.generate_content(prompt)
            return response.text
        except Exception as error:
            print(f"Error getting Gemini response: {error}")
            return "I'm having trouble connecting to my AI services. Let's check in again later, but in the meantime, remember to take breaks when needed and stay focused on your priorities."
    
    async def check_status(self):
        self.last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_time = datetime.now()
        
        channel = self.bot.get_channel(CONFIG["STATUS_CHANNEL_ID"])
        if not channel:
            print(f"Manager Bot: Could not find channel with ID {CONFIG['STATUS_CHANNEL_ID']}")
            print("Manager Bot: Skipping this status check.")
            return
            
        embed = discord.Embed(
            title="📊 Status Check",
            description="Let me know what you're currently working on.",
            color=0x3498db,
            timestamp=current_time
        )
        embed.add_field(name="Instructions", value="Please share what you're working on, any challenges you're facing, and how you're feeling about your progress.", inline=False)
        embed.set_footer(text="Reply to this message with your status update")
        
        status_message = await channel.send(embed=embed)
        def check(message):
            return message.channel == channel and not message.author.bot and message.reference and message.reference.message_id == status_message.id
        
        try:
            response = await self.bot.wait_for('message', check=check, timeout=1800)
            manager_response = await self.get_gemini_response(response.content)
            self.conversations.append({
                "timestamp": self.last_check_time,
                "user": str(response.author),
                "user_status": response.content,
                "bot_response": manager_response
            })
        
            embed = discord.Embed(
                title="💬 Manager Feedback",
                description=manager_response,
                color=0x9b59b6,
                timestamp=current_time
            )
            embed.set_footer(text="Respond to stay motivated and accountable")
            
            await channel.send(embed=embed)
            self.save_state()
            
        except asyncio.TimeoutError:
            await channel.send("No status update received. I'll check in again later!")

class JobTracker:
    def __init__(self, bot):
        self.bot = bot
        self.applications = []
        self.last_check_date = None
        self.job_task = None
        self.load_state()
        self.schedule_daily_check()
    
    def load_state(self):
        try:
            data_file = Path(CONFIG["DATA_DIR"]) / "job_applications.json"
            if data_file.exists():
                with open(data_file, 'r') as f:
                    state = json.load(f)
                    self.applications = state.get('applications', [])
                    self.last_check_date = state.get('last_check_date')
                print("Job Tracker: Data loaded successfully")
            else:
                print("Job Tracker: No previous data found, starting fresh")
        except Exception as error:
            print(f"Job Tracker: Error loading data: {error}")
    
    def save_state(self):
        try:
            data_file = Path(CONFIG["DATA_DIR"]) / "job_applications.json"
            with open(data_file, 'w') as f:
                json.dump({
                    "applications": self.applications,
                    "last_check_date": self.last_check_date
                }, f)
        except Exception as error:
            print(f"Job Tracker: Error saving data: {error}")
    
    def schedule_daily_check(self):
        if self.job_task:
            self.job_task.cancel()
        
        now = datetime.now()
        target_time = now.replace(hour=20, minute=0, second=0)
        if now > target_time:
            target_time += timedelta(days=1)
        
        seconds_until_target = (target_time - now).total_seconds()
        self.job_task = asyncio.create_task(self.schedule_job_check(seconds_until_target))
        print(f"Job Tracker: Check scheduled for {target_time.strftime('%Y-%m-%d %H:%M')}")
    
    async def schedule_job_check(self, seconds_delay):
        await asyncio.sleep(seconds_delay)
        await self.check_applications()
        await asyncio.sleep(24 * 60 * 60)
        self.schedule_daily_check()
    
    async def get_application_analysis(self, jobs_applied):
        try:
            model = genai.GenerativeModel('gemini-pro')
            
            prompt = f"""
            As a job search coach, analyze these job applications from today:
            {chr(10).join(jobs_applied)}
            
            Provide:
            1. A brief analysis of the job types/industries they're targeting
            2. One practical suggestion to improve their application success rate
            3. Positive encouragement about their job search process
            
            Keep it concise (3-4 sentences) and genuinely helpful.
            """
            
            response = model.generate_content(prompt)
            return response.text
        except Exception as error:
            print(f"Error getting Gemini analysis: {error}")
            return "Great job applying to these positions! Keep tracking your applications and following up when appropriate. Remember that job searching is a numbers game - persistence is key to finding the right opportunity."
    
    async def check_applications(self):
        current_time = datetime.now()
        self.last_check_date = current_time.strftime("%Y-%m-%d")
        
        channel = self.bot.get_channel(CONFIG["JOB_CHANNEL_ID"])
        if not channel:
            print(f"Job Tracker: Could not find channel with ID {CONFIG['JOB_CHANNEL_ID']}")
            print("Job Tracker: Skipping this application check.")
            return
        
        # Job application tracker message
        embed = discord.Embed(
            title="💼 Job Application Tracker",
            description="Please list all jobs you applied to today.",
            color=0xe74c3c,
            timestamp=current_time
        )
        embed.add_field(name="Instructions", value="Reply to this message with each job application on a new line. When you're done, reply with 'done' in a separate message.", inline=False)
        embed.add_field(name="Format", value="Company Name - Position", inline=False)
        embed.set_footer(text=f"Date: {self.last_check_date}")
        
        tracker_message = await channel.send(embed=embed)
        
        jobs_applied = []
        collecting = True
        
        def check(message):
            return message.channel == channel and not message.author.bot and message.reference and message.reference.message_id == tracker_message.id
        
        while collecting:
            try:
                response = await self.bot.wait_for('message', check=check, timeout=1800)
                
                if response.content.lower() in ['done', 'finished', 'complete', 'end']:
                    collecting = False
                else:
                    # Add the job to our list
                    jobs_applied.append(response.content)
                    await response.add_reaction('✅')
            except asyncio.TimeoutError:
                await channel.send("No response received for 30 minutes. Closing job application tracking for today.")
                collecting = False
        
        if not jobs_applied:
            # No applications today
            embed = discord.Embed(
                title="📝 No Applications Today",
                description="No applications recorded today. Remember, consistent application is key to finding opportunities!",
                color=0xf39c12,
                timestamp=current_time
            )
            await channel.send(embed=embed)
        else:
            # Add new applications to our list
            new_applications = [{
                "company": job,
                "date": self.last_check_date,
                "status": "Applied",
                "notes": ""
            } for job in jobs_applied]
            
            self.applications.extend(new_applications)
            
            # Get analysis from Gemini
            analysis = await self.get_application_analysis(jobs_applied)
            
            # Send analysis
            embed = discord.Embed(
                title=f"📊 Application Analysis ({len(jobs_applied)} jobs)",
                description=analysis,
                color=0x2ecc71,
                timestamp=current_time
            )
            
            # Add fields for each job
            for i, job in enumerate(jobs_applied, 1):
                embed.add_field(name=f"Job {i}", value=job, inline=False)
            
            embed.set_footer(text="Keep up the great work on your job search!")
            
            await channel.send(embed=embed)
        
        self.save_state()

# Function to send welcome message to a channel
async def send_welcome_message(channel_id, channel_name):
    """Send a welcome message to a specific channel"""
    channel = bot.get_channel(channel_id)
    if not channel:
        print(f"Could not send welcome message to {channel_name} channel (ID: {channel_id})")
        return False
    
    try:
        current_time = datetime.now()
        embed = discord.Embed(
            title="👋 Hello There!",
            description="I'm your Productivity Assistant Bot! Nice to meet you!",
            color=0x1abc9c,
            timestamp=current_time
        )
        
        embed.add_field(
            name="What I Can Do", 
            value="I'll help you stay productive with reminders, status checks, and job application tracking.", 
            inline=False
        )
        
        embed.add_field(
            name="Getting Started", 
            value="Type `!help` to see all available commands.", 
            inline=False
        )
        
        embed.set_footer(text="I'm here to help you succeed!")
        
        await channel.send(embed=embed)
        print(f"✓ Welcome message sent to {channel_name} channel")
        return True
    except Exception as e:
        print(f"✗ Error sending welcome message to {channel_name} channel: {e}")
        return False


@bot.event
async def on_ready():
    print(f'Bot logged in as {bot.user.name} ({bot.user.id})')
    
    # Check channel access before initializing
    channels_ok = await check_channels()
    
    if not channels_ok:
        print("\n⚠️ WARNING: Some channels are inaccessible. The bot will continue to run, but some features may not work properly.")
        print("Please check the channel IDs in your .env file and ensure the bot has proper permissions.\n")
    
    # Send welcome messages to all channels
    print("\nSending welcome messages to channels...")
    channels_with_welcome = 0
    
    # Try to send welcome messages to all three channels
    channels = [
        (CONFIG["REMINDER_CHANNEL_ID"], "Reminder"),
        (CONFIG["STATUS_CHANNEL_ID"], "Status"),
        (CONFIG["JOB_CHANNEL_ID"], "Job Tracker")
    ]
    
    for channel_id, channel_name in channels:
        if await send_welcome_message(channel_id, channel_name):
            channels_with_welcome += 1
    
    print(f"Welcome messages sent to {channels_with_welcome} out of {len(channels)} channels\n")
    
    # Initialize all bots after bot is ready
    global reminder_bot, manager_bot, job_tracker
    reminder_bot = ReminderBot(bot)
    manager_bot = ManagerBot(bot)
    job_tracker = JobTracker(bot)
    
    print("All systems initialized successfully!")

@bot.command(name='remind')
async def remind_command(ctx):
    await reminder_bot.send_reminder()
    await ctx.send("Manual reminder sent!")


@bot.command(name='status')
async def status_command(ctx):
    await manager_bot.check_status()
    await ctx.send("Manual status check initiated!")

@bot.command(name='jobs')
async def jobs_command(ctx):
    await job_tracker.check_applications()
    await ctx.send("Manual job application tracker initiated!")

@bot.command(name='help')
async def help_command(ctx):
    embed = discord.Embed(
        title="ProductivityPal Help",
        description="Here are the available commands:",
        color=0x3498db
    )
    
    embed.add_field(name="!remind", value="Trigger a manual reminder", inline=False)
    embed.add_field(name="!status", value="Trigger a manual status check", inline=False)
    embed.add_field(name="!jobs", value="Trigger the job application tracker", inline=False)
    embed.add_field(name="!help", value="Show this help message", inline=False)
    embed.add_field(name="!diagnose", value="Check bot health and diagnose issues", inline=False)
    embed.add_field(name="!welcome", value="Resend the welcome message", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='diagnose')
async def diagnose_command(ctx):
    """Command to check bot health and diagnose issues"""
    embed = discord.Embed(
        title="🔍 Bot Diagnostics",
        description="Checking bot systems and channel access...",
        color=0xf1c40f,
        timestamp=datetime.now()
    )
    
    # Check channel access
    channels_ok = await check_channels()
    
    embed.add_field(
        name="Channel Access", 
        value="✅ All channels accessible" if channels_ok else "❌ Some channels inaccessible", 
        inline=False
    )
    
    # Add environment variable status (without showing actual values)
    env_vars = {
        "DISCORD_TOKEN": bool(CONFIG["DISCORD_TOKEN"]),
        "GEMINI_API_KEY": bool(CONFIG["GEMINI_API_KEY"]),
        "REMINDER_CHANNEL_ID": CONFIG["REMINDER_CHANNEL_ID"] != 0,
        "STATUS_CHANNEL_ID": CONFIG["STATUS_CHANNEL_ID"] != 0,
        "JOB_CHANNEL_ID": CONFIG["JOB_CHANNEL_ID"] != 0
    }
    
    env_status = "\n".join([f"{'✅' if value else '❌'} {key}" for key, value in env_vars.items()])
    embed.add_field(name="Environment Variables", value=env_status, inline=False)
    
    # Data directory check
    data_dir = Path(CONFIG["DATA_DIR"])
    data_files = list(data_dir.glob("*.json"))
    embed.add_field(
        name="Data Files", 
        value=f"Found {len(data_files)} data files in {CONFIG['DATA_DIR']}" if data_dir.exists() else "❌ Data directory not found", 
        inline=False
    )

    # Check if HTTP server is running
    try:
        response = requests.get(f"http://localhost:{CONFIG['PORT']}")
        http_status = f"✅ HTTP server running (Status: {response.status_code})"
    except:
        http_status = "❌ HTTP server not responding"
        
    embed.add_field(name="HTTP Health Check", value=http_status, inline=False)
    
    # Check keep-alive mechanism
    embed.add_field(
        name="Keep-Alive Service", 
        value=f"✅ Running (pinging every {CONFIG['PING_INTERVAL_MINUTES']} minutes)", 
        inline=False
    )
    
    embed.set_footer(text="Run !help for available commands")
    
    await ctx.send(embed=embed)

@bot.command(name='welcome')
async def welcome_command(ctx):
    """Resend the welcome message to the current channel"""
    await send_welcome_message(ctx.channel.id, ctx.channel.name)
    await ctx.send("Welcome message sent!")


if __name__ == "__main__":
    try:
        print("Starting ProductivityPal Discord Bot...")
        print(f"Data directory: {CONFIG['DATA_DIR']}")
        
        # Start HTTP server in a separate thread
        http_thread = threading.Thread(target=run_http_server, daemon=True)
        http_thread.start()
        print(f"HTTP server started on port {CONFIG['PORT']}")
        
        # Start keep-alive mechanism in a separate thread
        keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
        keep_alive_thread.start()
        print(f"Keep-alive service started (interval: {CONFIG['PING_INTERVAL_MINUTES']} minutes)")
        
        # Run the bot
        bot.run(CONFIG["DISCORD_TOKEN"])
    except discord.errors.LoginFailure:
        print("⚠️ ERROR: Invalid Discord token. Please check your .env file.")
    except Exception as e:
        print(f"⚠️ ERROR: {e}")
