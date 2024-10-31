import os
import re
import json
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from ..http.automail import get_pin
from playwright_stealth import stealth_async
from ..utils.playwright_install import install
from playwright.async_api import Playwright, async_playwright, expect


async def fill_form(page, email, password):
    email_selector = 'input.input-control-cabinet__input[type="email"]'
    password_selector = 'input.input-control-cabinet__input[type="password"]'
    login_button_selector = 'button.button--primary[type="submit"]'

    await page.locator(email_selector).wait_for(state='visible')
    await page.locator(email_selector).fill(email)

    await page.locator(password_selector).wait_for(state='visible')
    await page.locator(password_selector).fill(password)

    await page.locator(login_button_selector).wait_for(state='visible')
    await page.locator(login_button_selector).click()


async def fill_code_form(page, code):
    code_selector = 'input.input-control-cabinet__input[name="code"]'
    login_button_selector = 'button.button--primary[type="submit"]'

    await page.locator(code_selector).wait_for(state='visible')
    await page.locator(code_selector).fill(code)

    await page.locator(login_button_selector).wait_for(state='visible')
    await page.locator(login_button_selector).click()


class Browser(object):
    user_data_dir = None
    base_url = 'qxbroker.com'
    https_base_url = f'https://{base_url}'
    email = None
    password = None
    email_pass = None
    args = [
        '--disable-web-security',
        '--no-sandbox',
        '--aggressive-cache-discard',
        '--disable-cache',
        '--disable-application-cache',
        '--disable-offline-load-stale-cache',
        '--disk-cache-size=0',
        '--disable-background-networking',
        '--disable-default-apps',
        '--disable-extensions',
        '--disable-sync',
        '--disable-translate',
        '--hide-scrollbars',
        '--metrics-recording-only',
        '--mute-audio',
        '--safebrowsing-disable-auto-update',
        '--ignore-certificate-errors',
        '--ignore-ssl-errors',
        '--ignore-certificate-errors-spki-list',
        '--disable-features=LeakyPeeker',
        '--disable-setuid-sandbox'
    ]

    def __init__(self, api):
        self.api = api
        self.html = None

    async def run(self, playwright: Playwright) -> None:
        try:
            if self.user_data_dir:
                browser = playwright.firefox
                context = await browser.launch_persistent_context(
                    self.user_data_dir,
                    args=self.args,
                    user_agent="Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0",
                    headless=True,
                    viewport={
                        "width": 1280,
                        "height": 720,
                    }
                )
                page = context.pages[0]
            else:
                browser = await playwright.firefox.launch(
                    headless=True,
                    args=self.args,
                )
                context = await browser.new_context(
                    viewport={
                        "width": 1280,
                        "height": 720
                    },
                    user_agent='Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0',
                )
                page = await context.new_page()

            await stealth_async(page)
            url = f"{self.https_base_url}/{self.api.lang}/sign-in/modal/"
            await page.goto(url=url)
            await page.wait_for_timeout(2000)  # Brief wait for page load

            # Check if already on trade page
            await page.wait_for_selector('body', timeout=10000)  # Wait for body to load
            if page.url != f"{self.https_base_url}/{self.api.lang}/trade":
                await fill_form(page, self.email, self.password)
                async with page.expect_navigation():
                    await page.wait_for_timeout(2000)  # Allow some time for navigation

                # Check if PIN code is required
                required_keep_code = await page.query_selector('input[name="keep_code"]')
                if required_keep_code:
                    auth_body = await page.query_selector("main.auth__body")
                    input_message = (
                        f'{await (await auth_body.query_selector("p")).inner_text()}: ' if auth_body and await auth_body.query_selector("p")
                        else "Enter the PIN code we just sent to your email: "
                    )

                    pin_code = None
                    if self.email_pass:
                        pin_code = await get_pin(self.email, self.email_pass)
                    code = pin_code or input(input_message)
                    await fill_code_form(page, code)

            await page.wait_for_timeout(2000)  # Allow time for the login process
            cookies = await context.cookies()
            source = await page.content()
            self.html = BeautifulSoup(source, "html.parser")
            user_agent = await page.evaluate("() => navigator.userAgent;")
            self.api.session_data["user_agent"] = user_agent

            # Process login success
            status, message = self.success_login()
            if not status:
                await context.close() if self.user_data_dir else await browser.close()
                return

            # Extract token
            settings = self.html.find_all("script", {"type": "text/javascript"})
            if settings and len(settings) > 1:
                token_data = settings[1].get_text().strip().replace(";", "")
                match = re.sub("window.settings = ", "", token_data)
                token = json.loads(match).get("token")
                self.api.session_data["token"] = token

                # Save cookies and token
                output_file = Path(os.path.join(self.api.resource_path, "session.json"))
                output_file.parent.mkdir(exist_ok=True, parents=True)
                cookiejar = requests.utils.cookiejar_from_dict({c['name']: c['value'] for c in cookies})
                cookies_string = '; '.join([f'{c.name}={c.value}' for c in cookiejar])
                self.api.session_data["cookies"] = cookies_string
                output_file.write_text(
                    json.dumps({"cookies": cookies_string, "token": token, "user_agent": user_agent}, indent=4)
                )
            else:
                print("Token extraction failed. No settings found.")

            await context.close() if self.user_data_dir else await browser.close()

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            print(f"Connection error occurred: {e}")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")

    def success_login(self):
        match = self.html.find(
            "div", {"class": "hint -danger"}
        ) or self.html.find(
            "div", {"class": "hint hint--danger"}
        )
        if match is None:
            return True, "Login successful."

        return False, f"Login failed. {match.text.strip()}"

    async def main(self) -> None:
        async with async_playwright() as playwright:
            # install(playwright.firefox, with_deps=True)
            await self.run(playwright)

    async def get_cookies_and_ssid(self):
        await self.main()
        return self.success_login()