"""Optional Playwright smoke check against the isolated tests.serve_fixture server."""
from pathlib import Path
import asyncio
from playwright.async_api import async_playwright


async def main():
    output = Path(__file__).resolve().parents[1] / 'docs/screenshots'
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto('http://127.0.0.1:8092/')
        await page.get_by_role('heading', name='Пока чистый лист').wait_for()
        await page.screenshot(path=str(output / 'catalog-desktop.png'), full_page=True)
        await page.get_by_role('button', name='В корзину').first.click()
        await page.get_by_role('button', name='Выбрать срок').first.click()
        await page.get_by_role('heading', name='Когда нужен конспект?').wait_for()
        await page.screenshot(path=str(output / 'checkout-desktop.png'), full_page=True)
        await page.get_by_role('button', name='Управление', exact=True).click()
        await page.get_by_role('heading', name='Рабочий стол магазина.').wait_for()
        await page.locator('.order-card').first.wait_for()
        await page.screenshot(path=str(output / 'admin-desktop.png'), full_page=True)
        await page.set_viewport_size({'width': 390, 'height': 844})
        await page.get_by_role('button', name='Каталог', exact=True).click()
        await page.locator('.service-card').first.wait_for()
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile overflow'
        await page.screenshot(path=str(output / 'catalog-mobile.png'), full_page=True)
        assert not errors, errors
        await browser.close()
        print('Desktop catalog, checkout, admin and mobile checked; no JS errors or mobile overflow.')


asyncio.run(main())
