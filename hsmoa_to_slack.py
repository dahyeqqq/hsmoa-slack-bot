# requirements:
#   pip install playwright requests
#   python -m playwright install --with-deps chromium
import os, asyncio, datetime, re, requests
from playwright.async_api import async_playwright

SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

# 필터(텍스트/로고)
HSMOA_SHOP = (os.environ.get("HSMOA_SHOP") or "").strip()
HSMOA_SHOP_LOGO = (os.environ.get("HSMOA_SHOP_LOGO") or "").strip()
HSMOA_CATEGORY = (os.environ.get("HSMOA_CATEGORY") or "").strip()
HSMOA_CATEGORY_LOGO = (os.environ.get("HSMOA_CATEGORY_LOGO") or "").strip()

KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(KST).strftime("%Y-%m-%d")
BASE_URL = "https://hsmoa.com/"

def clean(s): return re.sub(r"\s+", " ", s or "").strip()

async def click_by_text_or_label(page, text:str=None, logo_pat:str=None):
    ok = False
    if text:
        try:
            await page.get_by_text(text, exact=False).first.click(timeout=2000)
            ok = True
        except: pass
    if (not ok) and logo_pat:
        pat = re.compile(logo_pat, re.I)
        # img[alt]
        for el in await page.locator("img[alt]").all():
            alt = await el.get_attribute("alt") or ""
            if pat.search(alt):
                try: await el.click(timeout=2000); return True
                except: pass
        # [aria-label] / [title]
        for el in await page.locator("[aria-label], [title]").all():
            lab = (await el.get_attribute("aria-label")) or (await el.get_attribute("title")) or ""
            if pat.search(lab):
                try: await el.click(timeout=2000); return True
                except: pass
    return ok

async def auto_scroll(page, steps=6, wait_ms=400):
    for _ in range(steps):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(wait_ms)

async def scrape():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-blink-features=AutomationControlled"]
        )
        ctx = await browser.new_context(
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
        )
        page = await ctx.new_page()
        page.set_default_timeout(20000)

        await page.goto(BASE_URL, wait_until="networkidle")
        # 오늘 탭 클릭 시도
        try: await page.get_by_text("오늘", exact=False).first.click(timeout=1500)
        except: pass

        # 홈쇼핑사/카테고리 클릭(텍스트 → 로고 순서)
        await click_by_text_or_label(page, HSMOA_SHOP, HSMOA_SHOP_LOGO)
        await click_by_text_or_label(page, HSMOA_CATEGORY, HSMOA_CATEGORY_LOGO)

        # 로딩 + 스크롤로 DOM 채우기
        await page.wait_for_load_state("networkidle")
        await auto_scroll(page, steps=8, wait_ms=500)

        # 1차: 대표 셀렉터 후보들 순차 시도
        selector_sets = [
            "[data-testid='schedule-item']",
            "li.schedule-item, li:has(.time)",
            ".schedule-list .schedule-item, .list .item",
            ".row:has(.time)"
        ]
        rows = []
        for sel in selector_sets:
            nodes = await page.locator(sel).all()
            if nodes:
                rows = nodes; break

        items = []
        def add_item(t, title, ch, price):
            t, title, ch, price = clean(t), clean(title), clean(ch), clean(price)
            if not (t or title): return
            # 홈쇼핑사 텍스트 필터
            if HSMOA_SHOP and HSMOA_SHOP not in ch:
                # 로고 패턴으로 보정
                if HSMOA_SHOP_LOGO and not re.search(HSMOA_SHOP_LOGO, title, re.I):
                    return
            # 카테고리(로고만 쓴 경우엔 후처리 생략)
            items.append({"time": t or "-", "title": title or "(상품명)", "channel": ch, "price": price})

        if rows:
            for r in rows[:300]:
                t = ""
                title = ""
                ch = ""
                price = ""
                try:
                    if await r.locator(".time").count():   t = await r.locator(".time").inner_text()
                    if await r.locator(".title").count():  title = await r.locator(".title").inner_text()
                    if await r.locator(".channel").count():ch = await r.locator(".channel").inner_text()
                    if await r.locator(".price").count():  price = await r.locator(".price").inner_text()
                except: pass
                add_item(t, title, ch, price)

        # 2차: 최후 수단 — 시간 패턴으로 긁기 (예: 06:30)
        if not items:
            html = await page.content()
            # 행 단위로 대충 나눔
            for m in re.finditer(r"(\d{1,2}:\d{2}).{0,80}?<\/?[^>]*>([^<]{2,100})", html, re.S):
                t = m.group(1)
                title = clean(re.sub("<[^>]+>"," ", m.group(2)))
                add_item(t, title, "", "")

        await browser.close()
        return items

def build_text(items):
    filt_parts = []
    if HSMOA_SHOP or HSMOA_SHOP_LOGO: filt_parts.append(HSMOA_SHOP or HSMOA_SHOP_LOGO)
    if HSMOA_CATEGORY or HSMOA_CATEGORY_LOGO: filt_parts.append(HSMOA_CATEGORY or HSMOA_CATEGORY_LOGO)
    header = f"*{TODAY} 홈쇼핑모아 편성 – {' · '.join(filt_parts) if filt_parts else '전체'}*"

    lines = [header]
    if not items:
        lines.append("_데이터 없음(필터/셀렉터 확인 필요)_")
    else:
        # 디버그 요약
        sample = " | ".join([clean(i['title'])[:30] for i in items[:3]])
        lines.append(f"_총 {len(items)}건 · 샘플: {sample}_")
        for e in items[:60]:
            price = f" · {e['price']}" if e.get("price") else ""
            ch = f"[{e['channel']}]" if e.get("channel") else ""
            lines.append(f"• `{e['time']}` {ch} {e['title']}{price}")
    return "\n".join(lines)

def post_to_slack(text):
    try:
        requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": text, "blocks":[{"type":"section","text":{"type":"mrkdwn","text":text}}]},
            timeout=20
        ).raise_for_status()
    except Exception as e:
        print(f"[ERROR] Slack post failed: {e}")

async def main():
    items = await scrape()
    print(f"[DEBUG] scraped items count = {len(items)}")
    post_to_slack(build_text(items))

if __name__ == "__main__":
    asyncio.run(main())

