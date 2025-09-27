import asyncio
import aiohttp
import logging
import random
import os
import time
import sys
import json
from pathlib import Path
import cloudscraper
import aiofiles
from enum import Enum

# Global constants
DOMAIN_API = {
    'SESSION': 'http://api.nodepay.ai/api/auth/session',
    'PING': [
        "https://nw.nodepay.ai/api/network/ping",
    ]
}

PING_INTERVAL = 180  # seconds

# Connection states
class ConnectionStates(Enum):
    CONNECTED = 1
    DISCONNECTED = 2
    NONE_CONNECTION = 3

# Create scraper instance
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

def create_logger(account_identifier):
    """Create logger with account prefix"""
    logger = logging.getLogger(f'token:{account_identifier}')
    if logger.handlers:  # Avoid adding multiple handlers
        return logger
        
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '%(asctime)s | [%(name)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

def get_random_user_agent():
    """Get random user agent"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0',
    ]
    return random.choice(user_agents)

async def get_ip_address(proxy):
    """Get IP address using proxy"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.ipify.org?format=json', 
                                 proxy=proxy, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('ip')
    except Exception as e:
        return None

class AccountSession:
    def __init__(self, token, account_id):
        self.account_id = account_id
        self.token = token
        self.browser_stats = []  # Renamed for clarity
        self.account_info = {}
        self.proxy_auth_status = False
        self.status_connect = ConnectionStates.NONE_CONNECTION
        self.retries = 0
        self.last_ping_time = 0
        self.proxies = []
        self.user_agent = get_random_user_agent()
        self.logger = create_logger(account_id)
        self.session = None

    async def init(self):
        """Initialize account session"""
        try:
            await self.get_proxies()
            await self.initialize_browser_stats()
            await self.authenticate()
            await self.ping()
            self.start_ping_loop()
        except Exception as error:
            self.logger.error(f"Initialization error: {error}")

    async def initialize_browser_stats(self):
        """Initialize browser statistics for each proxy"""
        self.browser_stats = []
        for i in range(len(self.proxies)):
            self.browser_stats.append({
                'browser_id': f"browser_{i}_{int(time.time())}",
                'ping_count': 0,
                'successful_pings': 0,
                'score': 0,
                'start_time': time.time(),
                'last_ping_time': None
            })

    async def get_proxies(self):
        """Load proxies from file"""
        try:
            account_proxy_path = Path(f'./proxies/{self.account_id}.txt')
            proxy_data = ''
            
            if account_proxy_path.is_file():
                async with aiofiles.open(account_proxy_path, 'r') as f:
                    proxy_data = await f.read()
            else:
                root_proxy_path = Path('./proxies.txt')
                self.logger.info(f"Account proxy file not found, trying {root_proxy_path}")
                if root_proxy_path.is_file():
                    async with aiofiles.open(root_proxy_path, 'r') as f:
                        proxy_data = await f.read()
                else:
                    raise FileNotFoundError('No proxy files found')
            
            self.proxies = [line.strip() for line in proxy_data.splitlines() if line.strip()]
            if not self.proxies:
                raise ValueError('No valid proxies found')
                
            self.logger.info(f"Loaded {len(self.proxies)} proxies")
            
        except Exception as error:
            self.logger.error(f"Failed to load proxies: {error}")
            raise

    async def authenticate(self):
        """Authenticate with each proxy"""
        for i, proxy in enumerate(self.proxies):
            try:
                if self.proxy_auth_status:
                    continue
                    
                self.logger.info(f"Authenticating with proxy {i+1}/{len(self.proxies)}")
                ip_address = await get_ip_address(proxy)
                self.logger.info(f"Proxy IP: {ip_address}")

                response = await self.perform_request(DOMAIN_API['SESSION'], {}, proxy)
                if not response:
                    continue

                if response.get('code') != 0:
                    self.logger.error(f"Auth failed: {response.get('message', 'Unknown error')}")
                    continue

                self.account_info = response.get('data', {})
                if 'uid' in self.account_info:
                    self.proxy_auth_status = True
                    self.save_session_info()
                    self.logger.info(f"Authenticated successfully with UID: {self.account_info['uid']}")
                    break
                else:
                    self.logger.error("UID not found in response")
                    
            except Exception as error:
                self.logger.error(f"Authentication failed with proxy {proxy}: {error}")

    async def perform_request(self, url, data, proxy, max_retries=3):
        """Perform HTTP request with retry logic"""
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
            'Origin': 'chrome-extension://lgmpfmgeabnnlemejacfljbmonaomfmm',
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.5",
            "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": "Windows",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cors-site",
            "Priority": "u=1, i",
            "Referer": "https://app.nodepay.ai/",
        }

        for attempt in range(max_retries):
            try:
                proxies = {"http": proxy, "https": proxy} if proxy else None
                
                self.logger.debug(f"Request attempt {attempt+1} to {url}")

                # Use async execution for cloudscraper to avoid blocking
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None, 
                    lambda: scraper.post(url, json=data, headers=headers, proxies=proxies, timeout=30)
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    self.logger.warning(f"HTTP {response.status_code} for {url}")
                    if response.status_code == 403:
                        return None
                        
            except Exception as error:
                self.logger.error(f"Request failed (attempt {attempt+1}): {error}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    
        self.logger.error(f"All {max_retries} attempts failed for {url}")
        return None

    def start_ping_loop(self):
        """Start the ping loop"""
        self.logger.info(f"Starting ping loop with {PING_INTERVAL}s interval")
        asyncio.create_task(self.ping_loop())

    async def ping_loop(self):
        """Main ping loop"""
        while True:
            try:
                await self.ping()
            except Exception as error:
                self.logger.error(f"Ping loop error: {error}")
            await asyncio.sleep(PING_INTERVAL)

    async def ping(self):
        """Send ping to all endpoints"""
        current_time = time.time()
        
        # Check if enough time has passed since last ping
        if current_time - self.last_ping_time < PING_INTERVAL:
            self.logger.debug("Skipping ping - interval not reached")
            return

        self.last_ping_time = current_time

        for i, proxy in enumerate(self.proxies):
            if i >= len(self.browser_stats):
                self.logger.warning(f"No browser stats for proxy index {i}")
                continue
                
            try:
                browser_stat = self.browser_stats[i]
                browser_stat['last_ping_time'] = current_time
                
                data = {
                    'id': self.account_info.get('uid'),
                    'browser_id': browser_stat['browser_id'],
                    'timestamp': int(current_time * 1000),  # Convert to milliseconds
                }

                ping_success = False
                for ping_api in DOMAIN_API['PING']:
                    self.logger.info(f"Pinging {ping_api} with proxy {i+1}")
                    
                    response = await self.perform_request(ping_api, data, proxy)
                    browser_stat['ping_count'] += 1

                    if response and response.get('code') == 0:
                        self.retries = 0
                        self.status_connect = ConnectionStates.CONNECTED
                        ping_success = True
                        browser_stat['successful_pings'] += 1
                        
                        score = response.get('data', {}).get('ip_score', 0)
                        browser_stat['score'] = score
                        
                        self.logger.info(f"Ping successful! Score: {score}")
                        break
                    else:
                        error_msg = response.get('message', 'Unknown error') if response else 'No response'
                        self.logger.warning(f"Ping failed: {error_msg}")

                if not ping_success:
                    self.logger.error("All ping endpoints failed")
                    self.handle_ping_fail(proxy, response)
                    
            except Exception as error:
                self.logger.error(f"Ping error for proxy {proxy}: {error}")
                self.handle_ping_fail(proxy, None)

    def handle_ping_fail(self, proxy, response):
        """Handle ping failure"""
        self.retries += 1
        if response and response.get('code') == 403:
            self.handle_logout(proxy)
        elif self.retries >= 3:  # Increased threshold
            self.status_connect = ConnectionStates.DISCONNECTED
            self.logger.error("Max retries reached, marking as disconnected")

    def handle_logout(self, proxy):
        """Handle logout scenario"""
        self.status_connect = ConnectionStates.NONE_CONNECTION
        self.account_info = {}
        self.proxy_auth_status = False
        self.logger.warning(f"Logged out due to authentication issues")

    def save_session_info(self):
        """Save session information"""
        # Implement session saving logic here
        pass

    async def close(self):
        """Cleanup resources"""
        if self.session:
            await self.session.close()

async def load_tokens():
    """Load tokens from file"""
    try:
        async with aiofiles.open('tokens.txt', 'r') as f:
            tokens_data = await f.read()
            tokens = [line.strip().strip("'\"") for line in tokens_data.splitlines() if line.strip()]
            return tokens
    except Exception as error:
        print(f"Failed to load tokens: {error}")
        return []

async def main():
    """Main function"""
    print("""
     _  __        __    ___            ___       __
    / |/ /__  ___/ /__ / _ \\___ ___ __/ _ )___  / /_
   /    / _ \\/ _  / -_) ___/ _ `/ // / _  / _ \\/ __/
  /_/|_/\\___/\\_,_/\\__/_/   \\_,_/\\_, /____/\\___/\\__/
                               /___/
-----------------------------------------------------
|           NodePay bot by @overtrue                 |
|     Telegram: https://t.me/+ntyApQYvrBowZTc1       |
| GitHub: https://github.com/web3bothub/nodepay-bot  |
------------------------------------------------------
""")
    
    print('Starting program...')
    await asyncio.sleep(2)
    
    try:
        tokens = await load_tokens()
        if not tokens:
            print("No tokens found in tokens.txt")
            return

        sessions = []
        for index, token in enumerate(tokens, start=1):
            try:
                session = AccountSession(token, index)
                await session.init()
                sessions.append(session)
                self.logger.info(f"Session {index} initialized successfully")
                
                # Stagger initialization
                if index < len(tokens):
                    await asyncio.sleep(10)
                    
            except Exception as error:
                print(f"Failed to initialize session {index}: {error}")

        print(f"All sessions initialized. Total: {len(sessions)}")
        
        # Keep the program running
        await asyncio.Event().wait()
        
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as error:
        print(f"Program terminated: {error}")
    finally:
        # Cleanup
        for session in sessions:
            await session.close()

if __name__ == '__main__':
    # Configure root logger
    logging.basicConfig(level=logging.INFO)
    
    # Run the application
    asyncio.run(main())
