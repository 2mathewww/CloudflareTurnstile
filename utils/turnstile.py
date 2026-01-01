#!/usr/bin/env python3

import os
import time
import json
import random
import logging
import asyncio
from typing import Dict, Optional, Any
from camoufox.async_api import AsyncCamoufox
from patchright.async_api import async_playwright


class TurnstileSolver:
    
    HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Turnstile Solver</title>
        <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async></script>
        <style>
            body {
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
                background: #f0f0f0;
                font-family: Arial, sans-serif;
            }
            .container {
                text-align: center;
                padding: 30px;
                background: white;
                border-radius: 10px;
                box-shadow: 0 4px 20px rgba(0,0,0,0.1);
            }
            .turnstile-container {
                margin: 20px 0;
                display: inline-block;
            }
            #status {
                margin-top: 20px;
                padding: 10px;
                background: #f8f8f8;
                border-radius: 5px;
                font-size: 14px;
                color: #333;
            }
        </style>
        <script>
            function updateStatus(message, type = 'info') {
                const status = document.getElementById('status');
                status.textContent = message;
                status.style.color = type === 'error' ? '#d32f2f' : 
                                    type === 'success' ? '#388e3c' : '#1976d2';
            }
            
            function checkToken() {
                const tokenInput = document.querySelector('[name="cf-turnstile-response"]');
                if (tokenInput && tokenInput.value) {
                    updateStatus(`Token received (${tokenInput.value.length} chars)`, 'success');
                }
            }
            
            window.onload = function() {
                setInterval(checkToken, 500);
                updateStatus('Turnstile loading...');
            };
        </script>
    </head>
    <body>
        <div class="container">
            <h2>Cloudflare Turnstile Test</h2>
            <div class="turnstile-container">
                <!-- Turnstile will be inserted here -->
            </div>
            <div id="status">Initializing...</div>
        </div>
    </body>
    </html>
    """
    
    def __init__(self, headless: bool = True, thread: int = 2, 
                 browser_type: str = 'chromium', proxy_support: bool = False,
                 useragent: Optional[str] = None):
        
        self._logger = self._setup_logger()
        
        self.headless = headless
        self.thread_count = thread
        self.browser_type = browser_type
        self.proxy_support = proxy_support
        
        self.useragent = useragent if useragent else self._get_random_user_agent()
        self.browser_pool = asyncio.Queue()
        self.browser_args = []
        
        if self.useragent:
            self.browser_args.append(f"--user-agent={self.useragent}")
        
        self.browser_args.extend([
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-blink-features=AutomationControlled',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process'
        ])
        
        if self.headless:
            self.browser_args.extend(['--headless=new'])
        
        self._logger.info(f"Initialized: headless={headless}, threads={thread}, "
                         f"browser={browser_type}, ua={self.useragent[:50]}...")
    
    def _has_display(self) -> bool:
        return 'DISPLAY' in os.environ and os.environ['DISPLAY'] != ''
    
    def _get_random_user_agent(self) -> str:
        user_agents_path = "data/user-agents.txt"
        fallback_user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0"
        ]
        
        try:
            if os.path.exists(user_agents_path):
                with open(user_agents_path, 'r') as f:
                    user_agents = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                
                if user_agents:
                    selected_ua = random.choice(user_agents)
                    self._logger.info(f"Selected UA from file: {selected_ua[:50]}...")
                    return selected_ua
                else:
                    self._logger.warning("user-agents.txt empty, using fallback")
            else:
                self._logger.warning(f"{user_agents_path} not found, using fallback")
                
            selected_ua = random.choice(fallback_user_agents)
            self._logger.info(f"Selected fallback UA: {selected_ua[:50]}...")
            return selected_ua
            
        except Exception as e:
            self._logger.error(f"Error loading UAs: {str(e)}, using fallback")
            return random.choice(fallback_user_agents)
    
    def _setup_logger(self):
        logger = logging.getLogger("TurnstileSolver")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s [%(levelname)s] -> %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger
    
    async def initialize(self):
        self._logger.info(f"Starting browser initialization ({self.thread_count} threads)")
        
        try:
            if self.browser_type in ['chromium', 'chrome', 'msedge']:
                self.playwright = await async_playwright().start()
            elif self.browser_type == "camoufox":
                self.camoufox = AsyncCamoufox(headless=self.headless)
            else:
                raise ValueError(f"Unsupported browser type: {self.browser_type}")
            
            for i in range(self.thread_count):
                browser = await self._create_browser(i + 1)
                await self.browser_pool.put((i + 1, browser))
                self._logger.info(f"Browser {i + 1}/{self.thread_count} initialized")
            
            self._logger.info(f"Browser pool ready with {self.thread_count} browsers")
            return True
            
        except Exception as e:
            self._logger.error(f"Failed to initialize browser pool: {str(e)}")
            raise
    
    async def _create_browser(self, index: int):
        try:
            if self.browser_type in ['chromium', 'chrome', 'msedge']:
                browser = await self.playwright.chromium.launch(
                    channel=self.browser_type if self.browser_type != 'chromium' else None,
                    headless=self.headless,
                    args=self.browser_args
                )
            elif self.browser_type == "camoufox":
                browser = await self.camoufox.start()
            
            return browser
            
        except Exception as e:
            self._logger.error(f"Failed to create browser {index}: {str(e)}")
            if self.browser_type in ['chromium', 'chrome', 'msedge']:
                self._logger.warning("Trying fallback with headless mode...")
                browser = await self.playwright.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage']
                )
                return browser
            raise
    
    async def solve(self, url: str, sitekey: str, action: Optional[str] = None, 
                   cdata: Optional[str] = None) -> Dict[str, Any]:
        
        if not url or not sitekey:
            return {
                "status": "error",
                "error": "Both 'url' and 'sitekey' are required"
            }
        
        self._logger.info(f"Solving Turnstile: {url[:50]}...")
        
        start_time = time.time()
        
        index, browser = await self.browser_pool.get()
        
        try:
            context = await self._setup_context(browser)
            page = await context.new_page()
            
            result = await self._solve_on_page(page, url, sitekey, action, cdata, start_time)
            
            await context.close()
            await self.browser_pool.put((index, browser))
            
            return result
            
        except Exception as e:
            elapsed_time = round(time.time() - start_time, 3)
            await self.browser_pool.put((index, browser))
            
            return {
                "status": "error",
                "error": str(e),
                "time": elapsed_time
            }
    
    async def _setup_context(self, browser):
        if not self.proxy_support:
            return await browser.new_context()
        
        proxy_file_path = os.path.join(os.getcwd(), "data/proxies.txt")
        if os.path.exists(proxy_file_path):
            with open(proxy_file_path) as proxy_file:
                proxies = [line.strip() for line in proxy_file if line.strip()]
            
            proxy = random.choice(proxies) if proxies else None
            
            if proxy:
                parts = proxy.split(':')
                if len(parts) == 3:
                    return await browser.new_context(proxy={"server": f"{proxy}"})
                elif len(parts) == 5:
                    proxy_scheme, proxy_ip, proxy_port, proxy_user, proxy_pass = parts
                    return await browser.new_context(
                        proxy={
                            "server": f"{proxy_scheme}://{proxy_ip}:{proxy_port}",
                            "username": proxy_user,
                            "password": proxy_pass
                        }
                    )
        
        return await browser.new_context()
    
    async def _solve_on_page(self, page, url: str, sitekey: str, action: Optional[str], 
                           cdata: Optional[str], start_time: float) -> Dict[str, Any]:
        
        # Build Turnstile HTML
        turnstile_div = f'<div class="cf-turnstile" data-sitekey="{sitekey}"'
        if action:
            turnstile_div += f' data-action="{action}"'
        if cdata:
            turnstile_div += f' data-cdata="{cdata}"'
        turnstile_div += ' style="transform: scale(1.2);"></div>'
        
        page_data = self.HTML_TEMPLATE.replace("<!-- Turnstile will be inserted here -->", turnstile_div)
        
        # Route and navigate
        url_with_slash = url + "/" if not url.endswith("/") else url
        await page.route(url_with_slash, lambda route: route.fulfill(
            body=page_data, 
            status=200,
            headers={"Content-Type": "text/html"}
        ))
        
        await page.goto(url_with_slash)
        
        # Wait for Turnstile to load
        try:
            await page.wait_for_selector(".cf-turnstile", timeout=10000)
        except:
            raise Exception("Turnstile failed to load")
        
        # Wait for Cloudflare script
        await asyncio.sleep(2)
        
        # Click the Turnstile
        try:
            # Try to find and click iframe first
            iframe_locator = page.locator("iframe[title*='cloudflare']")
            if await iframe_locator.count() > 0:
                await iframe_locator.click(timeout=2000)
            else:
                await page.click(".cf-turnstile", timeout=2000)
        except:
            self._logger.warning("Direct click failed, trying JavaScript click")
            await page.evaluate("""() => {
                const turnstile = document.querySelector('.cf-turnstile');
                if (turnstile) turnstile.click();
            }""")
        
        # Wait for token
        for attempt in range(30):
            try:
                token = await page.evaluate("""() => {
                    const input = document.querySelector('[name="cf-turnstile-response"]');
                    return input ? input.value : null;
                }""")
                
                if token and token.strip():
                    elapsed_time = round(time.time() - start_time, 3)
                    self._logger.info(f"✅ Solved in {elapsed_time}s")
                    return {
                        "status": "success",
                        "token": token,
                        "time": elapsed_time
                    }
                
                # Wait and retry click
                await asyncio.sleep(1)
                
                if attempt % 5 == 0:
                    try:
                        await page.click(".cf-turnstile", timeout=1000)
                    except:
                        pass
                        
            except Exception as e:
                if attempt < 29:
                    await asyncio.sleep(1)
                else:
                    self._logger.error(f"Error on attempt {attempt}: {str(e)}")
        
        raise Exception("Failed to get Turnstile token after 30 attempts")
    
    async def cleanup(self):
        self._logger.info("Cleaning up...")
        
        while not self.browser_pool.empty():
            try:
                index, browser = await self.browser_pool.get()
                await browser.close()
            except:
                pass
        
        if hasattr(self, 'playwright'):
            await self.playwright.stop()
        
        self._logger.info("Cleanup completed")
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "initialized": not self.browser_pool.empty(),
            "thread_count": self.thread_count,
            "browser_type": self.browser_type,
            "headless": self.headless,
            "has_display": self._has_display(),
            "user_agent": self.useragent[:50] + "..." if len(self.useragent) > 50 else self.useragent,
            "pool_size": self.browser_pool.qsize()
        }