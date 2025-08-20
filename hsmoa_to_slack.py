# requirements:
#   pip install playwright requests
#   python -m playwright install --with-deps chromium
import os, asyncio, datetime, re, requests
from playwright.async_api import async_playwright

SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

# ← 텍스트 버튼은 안 쓰고 "로고/아이콘"만 씀 (정규식 | 로 OR)
HSMOA_SHOP_LOGO = (os.environ.get("HSMOA_SHOP_LOGO") or "").strip()
HSMOA_CATEGORY_LOGO = (os.environ.get("HSMOA_CATEGORY_LOGO") or "").strip()

KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(KST).strftime("%Y-%m-%d")
BASE_URL = "https://hsmoa.com/"

def clean(s): return re.sub(r"\s+", " ", s or "").strip()

# ---------- 클릭/검증 ----------
async def click_by_label_like(page, pattern: str) -> bool:
    """alt/title/aria-label에 pattern(정규식)이 들어간 첫 요소 클릭"""
    if not pattern: return False
    pat = re.compile(pattern, re.I)

    # img[alt]
    for el in await page.locator("img[alt]").all():
        alt = await el.get_attribute("alt") or ""
        if pat.search(alt):
            try:
                await el.click(timeout=2000)
                return True
            except:
                pass

    # [aria-label] 또는 [title]
    for el in await page.locator("[aria-label], [title]").all():
        lab = (await el.get_attribute("aria-label")) or (await el.get_attribute("title")) or ""
        if pat.search(lab):
            try:
                await el.click(timeout=2000)
                return True
            except:
                pass
    return False

async def is_filter_applied(page) -> bool:
    """선택된 필터(칩/버튼) 흔적 확인"""
    return (await page.locator(".active, .selected, [aria-pressed='true'], .on").count()) > 0

# ---------- 스크롤 ----------
async def scroll_to_bottom(page, max_rounds=25, wait_ms=500):
    """무한스크롤: 높이 변화 끝날 때까지"""
    last_h, stable = 0, 0
    for _ in range(max_rounds):
        h = await page.evaluate("document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(wait_ms)
        h2 = await page.evaluate("document.body.scrollHeight")
        if h2 == last_h: stable += 1
        else: stable = 0
        last_h = h2
        if stable >= 2: break
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(300)

# ---------- 파싱 ----------
async def robust_rows(page):
    """여러 셀렉터 후보로 행 수집"""
    selectors = [
        "[data-testid='schedule-item']",
        ".schedule-item",
        "li:has(.time)",
        ".row:has(.time)",
        "article:has(.time)"
    ]
    for sel in selectors:
        nodes = await page.locator(sel).all()
        if nodes: return nodes
    return []

async def parse_row(node):
    def pick(*cands):
        for c in cands:
            loc = node.locator(c)
            if asyncio.get_event_loop().run_until_complete(loc.count()):
                return asyncio.get_event_loop().run_until_complete(loc.first.inner_text())
        return ""
    # Playwright의 await 중첩 방지 위해 세분화
    t = ""
    for cls in [".time", ".broadcast-time", "[data-field='time']"]:
        if await node.locator(cls).count():
            t = clean(await node.locator(cls).first.inner_text()); break
    title = ""
    for cls in [".title", ".goods", ".item-title", "[data-field='title']"]:
        if await node.locator(cls).count():
            title = clean(await node.locator(cls).first.inner_text()); break
    ch = ""
    for cls in [".channel", ".ch", "[data-field='channel']"]:
        if await node.locator(cls).count():
            ch = clean(await node.locator(cls).first.inner_text()); break
    price = ""
    for cls in [".price", ".sale", ".amount", "[data-field='price']"]:
        if await node.locator(cls).count():
            price = clean(await node.locator(cls).first.inner_text()); break

    # 최후수단: node 전체 텍스트에서 시간, 제목 추정
    if not (t and title):
        txt = clean(await node.inner_text())
        if not t:
            m = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", txt)
            if m: t = m.group(0)
        if not title and txt:
            title = re.sub(r"\b([01]?\d|2[0-3]):[0-5]\d\b|원|LIVE|SHOP|채널|방송", "", txt)
            title = clean(title)[:120]

    if t or title:
        return {"time": t or "-", "title": title or "(상품명)", "channel": ch, "price": price}
    return None

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

        # '오늘' 탭(있으면) 클릭
        try: await page.get_by_text("오늘", exact=False).first.click(timeout=1500)
        except: pass

        # ① 홈쇼핑사 아이콘 클릭 + 적용 확인/재시도
        if HSMOA_SHOP_LOGO:
            await click_by_label_like(page, HSMOA_SHOP_LOGO)
            await page.wait_for_timeout(600)
            if not await is_filter_applied(page):
                await click_by_label_like(page, HSMOA_SHOP_LOGO)  # 재시도
                await page.wait_for_timeout(600)

        # ② 카테고리 아이콘 클릭 + 적용 확인/재시도
        if HSMOA_CATEGORY_LOGO:
            await click_by_label_like(page, HSMOA_CATEGORY_LOGO)
            await page.wait_for_timeout(600)
            if not await is_filter_applied(page):
                await click_by_label_like(page, HSMOA_CATEGORY_LOGO)  # 재시도
                await page.wait_for_timeout(600)

        # ③ 모든 항목 로드될 때까지 무한스크롤
        await scroll_to_bottom(page)

        # ④ 파싱
        rows = await robust_rows(page)
        items = []
        for r in rows[:400]:
            e = await parse_row(r)
            if e: items.append(e)

        await browser.close()
        return items

# ---------- Slack ----------
def build_text(items):
    filt_parts = []
    if HSMOA_SHOP_LOGO:     filt_parts.append(HSMOA_SHOP_LOGO)
    if HSMOA_CATEGORY_LOGO: filt_parts.append(HSMOA_CATEGORY_LOGO)
    header = f"*{TODAY} 홈쇼핑모아 편성 – {' · '.join(filt_parts) if filt_parts else '전체'}*"

    lines = [header]
    if not items:
        lines.append("_데이터 없음(필터/셀렉터 확인 필요)_")
    else:
        sample = " | ".join([clean(i['title'])[:30] for i in items[:3]])
        lines.append(f"_총 {len(items)}건 · 샘플: {sample}_")
        for e in items[:80]:
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
    post_to_slack(build_text(items))

if __name__ == "__main__":
    asyncio.run(main())

