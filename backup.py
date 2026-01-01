import os
import sys
import time
import uuid
import json
import random
import logging
import asyncio
import argparse
from quart import Quart, request, jsonify
from camoufox.async_api import AsyncCamoufox
from patchright.async_api import async_playwright


COLORS = {
    'MAGENTA': '\033[35m',
    'GREEN': '\033[32m',
    'RED': '\033[31m',
    'RESET': '\033[0m',
}


class CustomLogger(logging.Logger):
    @staticmethod
    def format_message(level, color, message):
        timestamp = time.strftime('%H:%M:%S')
        return f"[{timestamp}] [{COLORS.get(color)}{level}{COLORS.get('RESET')}] -> {message}"

    def info(self, message, *args, **kwargs):
        super().info(self.format_message('INFO', 'MAGENTA', message), *args, **kwargs)

    def success(self, message, *args, **kwargs):
        super().info(self.format_message('SUCCESS', 'GREEN', message), *args, **kwargs)

    def error(self, message, *args, **kwargs):
        super().error(self.format_message('ERROR', 'RED', message), *args, **kwargs)


logging.setLoggerClass(CustomLogger)
logger = logging.getLogger("TurnstileAPIServer")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)


class TurnstileAPIServer:
    HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Turnstile Solver</title>
        <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async></script>
        <script>
            async function fetchIP() {
                try {
                    const response = await fetch('https://api64.ipify.org?format=json');
                    const data = await response.json();
                    document.getElementById('ip-display').innerText = `Your IP: ${data.ip}`;
                } catch (error) {
                    console.error('Error fetching IP:', error);
                    document.getElementById('ip-display').innerText = 'Failed to fetch IP';
                }
            }
            window.onload = fetchIP;
        </script>
    </head>
    <body>
        <!-- cf turnstile -->
        <p id="ip-display">Fetching your IP...</p>
    </body>
    </html>
    """

    def __init__(self, headless: bool, useragent: str, browser_type: str, thread: int, proxy_support: bool):
        self.app = Quart(__name__)
        self.browser_type = browser_type
        self.headless = headless
        self.useragent = useragent
        self.thread_count = thread
        self.proxy_support = proxy_support
        self.browser_pool = asyncio.Queue()
        self.browser_args = []
        if useragent:
            self.browser_args.append(f"--user-agent={useragent}")

        self._setup_routes()

    def _setup_routes(self) -> None:
        """Set up the application routes."""
        self.app.before_serving(self._startup)
        self.app.route('/turnstile', methods=['GET'])(self.process_turnstile)
        self.app.route('/')(self.index)

    async def _startup(self) -> None:
        """Initialize the browser and page pool on startup."""
        logger.info("Starting browser initialization")
        try:
            await self._initialize_browser()
        except Exception as e:
            logger.error(f"Failed to initialize browser: {str(e)}")
            raise

    async def _initialize_browser(self) -> None:
        """Initialize the browser and create the page pool."""

        if self.browser_type in ['chromium', 'chrome', 'msedge']:
            playwright = await async_playwright().start()
        elif self.browser_type == "camoufox":
            camoufox = AsyncCamoufox(headless=self.headless)

        for _ in range(self.thread_count):
            if self.browser_type in ['chromium', 'chrome', 'msedge']:
                browser = await playwright.chromium.launch(
                    channel=self.browser_type,
                    headless=self.headless,
                    args=self.browser_args
                )
            elif self.browser_type == "camoufox":
                browser = await camoufox.start()

            await self.browser_pool.put((_+1, browser))

        logger.success(f"Browser pool initialized with {self.thread_count} browsers")

    async def _solve_turnstile(self, url: str, sitekey: str, action: str = None, cdata: str = None):
        """Solve the Turnstile challenge and return result."""
        proxy = None

        index, browser = await self.browser_pool.get()

        if self.proxy_support:
            proxy_file_path = os.path.join(os.getcwd(), "proxies.txt")

            with open(proxy_file_path) as proxy_file:
                proxies = [line.strip() for line in proxy_file if line.strip()]

            proxy = random.choice(proxies) if proxies else None

            if proxy:
                parts = proxy.split(':')
                if len(parts) == 3:
                    context = await browser.new_context(proxy={"server": f"{proxy}"})
                elif len(parts) == 5:
                    proxy_scheme, proxy_ip, proxy_port, proxy_user, proxy_pass = parts
                    context = await browser.new_context(proxy={"server": f"{proxy_scheme}://{proxy_ip}:{proxy_port}", "username": proxy_user, "password": proxy_pass})
                else:
                    raise ValueError("Invalid proxy format")
            else:
                context = await browser.new_context()
        else:
            context = await browser.new_context()

        page = await context.new_page()

        start_time = time.time()

        try:
            url_with_slash = url + "/" if not url.endswith("/") else url
            turnstile_div = f'<div class="cf-turnstile" style="background: white;" data-sitekey="{sitekey}"' + (f' data-action="{action}"' if action else '') + (f' data-cdata="{cdata}"' if cdata else '') + '></div>'
            page_data = self.HTML_TEMPLATE.replace("<!-- cf turnstile -->", turnstile_div)

            await page.route(url_with_slash, lambda route: route.fulfill(body=page_data, status=200))
            await page.goto(url_with_slash)

            await page.eval_on_selector("//div[@class='cf-turnstile']", "el => el.style.width = '70px'")

            for attempt in range(15):
                try:
                    turnstile_check = await page.input_value("[name=cf-turnstile-response]", timeout=2000)
                    if turnstile_check == "":
                        await page.locator("//div[@class='cf-turnstile']").click(timeout=1000)
                        await asyncio.sleep(0.5)
                    else:
                        elapsed_time = round(time.time() - start_time, 3)
                        
                        logger.success(f"Solved in {elapsed_time}s")
                        
                        await context.close()
                        await self.browser_pool.put((index, browser))
                        
                        return {
                            "status": "success",
                            "token": turnstile_check,
                            "time": elapsed_time
                        }
                except Exception:
                    if attempt < 14:
                        await asyncio.sleep(0.5)
                    else:
                        raise Exception("Failed to get Turnstile token after 15 attempts")

            raise Exception("Failed to get Turnstile token after 15 attempts")
            
        except Exception as e:
            elapsed_time = round(time.time() - start_time, 3)
            
            await context.close()
            await self.browser_pool.put((index, browser))
            
            return {
                "status": "error",
                "error": str(e),
                "time": elapsed_time
            }

    async def process_turnstile(self):
        """Handle the /turnstile endpoint requests and return result immediately."""
        url = request.args.get('url')
        sitekey = request.args.get('sitekey')
        action = request.args.get('action')
        cdata = request.args.get('cdata')

        if not url or not sitekey:
            return jsonify({
                "status": "error",
                "error": "Both 'url' and 'sitekey' are required"
            }), 400

        logger.info(f"Processing: {url[:50]}...")
        
        try:
            result = await self._solve_turnstile(
                url=url, 
                sitekey=sitekey, 
                action=action, 
                cdata=cdata
            )
            
            return jsonify(result), 200 if result["status"] == "success" else 422
            
        except Exception as e:
            return jsonify({
                "status": "error",
                "error": str(e)
            }), 500

    @staticmethod
    async def index():
        """Serve the API documentation page."""
        return """
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Turnstile Solver API</title>
                <script src="https://cdn.tailwindcss.com"></script>
            </head>
            <body class="bg-gray-900 text-gray-200 min-h-screen flex items-center justify-center">
                <div class="bg-gray-800 p-8 rounded-lg shadow-md max-w-2xl w-full border border-red-500">
                    <h1 class="text-3xl font-bold mb-6 text-center text-red-500">Turnstile Solver API</h1>

                    <p class="mb-4 text-gray-300">Send GET request to:</p>
                    <code class="bg-red-700 text-white px-3 py-2 rounded block mb-6">/turnstile?url=URL&sitekey=SITEKEY</code>

                    <div class="bg-gray-700 p-4 rounded-lg mb-6 border border-red-500">
                        <p class="font-semibold mb-2 text-red-400">Example:</p>
                        <code class="text-sm break-all text-red-300">/turnstile?url=https://example.com&sitekey=0x4AAAAAAABXjYF4QqIhS0hT</code>
                    </div>

                    <div class="bg-green-900 border-l-4 border-green-600 p-4 mb-6">
                        <p class="text-green-200 font-semibold">Returns result immediately</p>
                        <p class="text-green-300 text-sm">Token returned directly in response</p>
                    </div>

                    <div class="bg-red-900 border-l-4 border-red-600 p-4">
                        <p class="text-red-200 font-semibold text-sm">Maintained by Theyka & Sexfrance</p>
                    </div>
                </div>
            </body>
            </html>
        """


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Turnstile API Server")

    parser.add_argument('--headless', type=bool, default=False, help='Run browser in headless mode (default: False)')
    parser.add_argument('--useragent', type=str, default=None, help='Custom User-Agent string')
    parser.add_argument('--browser_type', type=str, default='chromium', help='Browser type: chromium, chrome, msedge, camoufox (default: chromium)')
    parser.add_argument('--thread', type=int, default=1, help='Number of browser threads (default: 1)')
    parser.add_argument('--proxy', type=bool, default=False, help='Enable proxy support (Default: False)')
    parser.add_argument('--host', type=str, default='127.0.0.1', help='Host IP (Default: 127.0.0.1)')
    parser.add_argument('--port', type=str, default='5000', help='Port (Default: 5000)')
    return parser.parse_args()


def create_app(headless: bool, useragent: str, browser_type: str, thread: int, proxy_support: bool) -> Quart:
    server = TurnstileAPIServer(headless=headless, useragent=useragent, browser_type=browser_type, thread=thread, proxy_support=proxy_support)
    return server.app


if __name__ == '__main__':
    args = parse_args()
    browser_types = ['chromium', 'chrome', 'msedge', 'camoufox']
    
    if args.browser_type not in browser_types:
        logger.error(f"Unknown browser type: {args.browser_type}")
    elif args.headless is True and args.useragent is None and "camoufox" not in args.browser_type:
        logger.error("You must specify a User-Agent for Turnstile Solver or use camoufox")
    else:
        app = create_app(
            headless=args.headless, 
            useragent=args.useragent, 
            browser_type=args.browser_type, 
            thread=args.thread, 
            proxy_support=args.proxy
        )
        app.run(host=args.host, port=int(args.port))
