# requirements:
#   pip install playwright requests
#   python -m playwright install --with-deps chromium
import os, asyncio, datetime, re, requests
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

# ✅ 환경변수로 필터 지정 (원하면 GitHub Secrets에 넣을 수 있음)
HSMOA_SHOP = os.environ.get("HSMOA_SHOP", "").strip()         # 예: "롯데홈쇼핑"
HSMOA_CATEGORY = os.environ.get("HSMOA_CATEGORY", "").strip() # 예: "언더웨어"

KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY_TXT = datetime.datetime.now(KST).strftime("%Y-%m-%d")

BASE_URL = "https://hsmoa.com/"

# ---------- 유틸 ----------
def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

async def click_if_exists(page, locator):
    try:
        await locator.first.click(timeout=2000)
        return True
    except PWTimeout:
        return False
    except Exception:
        return False

# ---------- 스크래핑 ----------
async def scrape_filtered_today():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(BASE_URL, wait_until="networkidle")

        # 1) '오늘' 탭
        await click_if_exists(page, page.get_by_text("오늘"))

        # 2) 홈쇼핑사 필터
        if HSMOA_SHOP:
            await click_if_exists(page, page.get_by_text(HSMOA_SHOP))

        # 3) 카테고리 필터
        if HSMOA_CATEGORY:
            await click_if_exists(page, page.get_by_text(HSMOA_CATEGORY))

        # 4) 렌더 대기
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(800)

        # 5) 항목 추출
        items = []
        rows = await page.locator("[data-testid='schedule-item']").all()
        if not rows:
            rows = await page.locator("li:has(.time), .schedule-item, .row:has(.time)").all()

        for r in rows[:200]:
            time_txt = clean(await r.locator(".time").inner_text()) if await r.locator(".time").count() else ""
            title_txt = clean(await r.locator(".title").inner_text()) if await r.locator(".title").count() else ""
            ch_txt = clean(await r.locator(".channel").inner_text()) if await r.locator(".channel").count() else ""
            price_txt = clean(await r.locator(".price").inner_text()) if await r.locator(".price").count() else ""

            if HSMOA_SHOP and HSMOA_SHOP not in ch_txt:
                continue
            if time_txt or title_txt:
                items.append({
                    "time": time_txt or "-",
                    "title": title_txt or "(상품명)",
                    "channel": ch_txt,
                    "price": price_txt
                })

        await browser.close()
        return items

# ---------- Slack ----------
def build_text(items):
    filt = []
    if HSMOA_SHOP: filt.append(HSMOA_SHOP)
    if HSMOA_CATEGORY: filt.append(HSMOA_CATEGORY)
    filt_txt = " · ".join(filt) if filt else "전체"

    lines = [f"*{TODAY_TXT} 홈쇼핑모아 편성 – {filt_txt}*"]
    if not items:
        lines.append("_데이터 없음(필터/셀렉터 확인 필요)_")
    else:
        for e in items[:50]:
            price = f" · {e['price']}" if e.get("price") else ""
            ch = f"[{e['channel']}]" if e.get("channel") else ""
            lines.append(f"• `{e['time']}` {ch} {e['title']}{price}")
    return "\n".join(lines)

def post_to_slack(text):
    payload = {"text": text, "blocks":[{"type":"section","text":{"type":"mrkdwn","text":text}}]}
    r = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=20)
    r.raise_for_status()

async def main():
    items = await scrape_filtered_today()
    post_to_slack(build_text(items))

if __name__ == "__main__":
    asyncio.run(main())
