# requirements:
#   pip install playwright requests
#   python -m playwright install --with-deps chromium
import os, asyncio, datetime, re, requests
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

# ✅ 필터: 텍스트/로고(alt) 둘 다 지원
# - HSMOA_SHOP: 채널 이름이 화면에 텍스트로 있을 때(있으면 우선 사용)
# - HSMOA_SHOP_LOGO: 로고 alt/title/aria-label 로 찾을 때 (정규식 패턴, | 로 구분)
#   예) 롯데홈쇼핑이면 "롯데|LOTTE|LOTTEON"
HSMOA_SHOP = os.environ.get("HSMOA_SHOP", "").strip()
HSMOA_SHOP_LOGO = os.environ.get("HSMOA_SHOP_LOGO", "").strip()

# - HSMOA_CATEGORY: 카테고리명이 텍스트일 때
# - HSMOA_CATEGORY_LOGO: 카테고리 아이콘 alt/title/aria-label 로 찾을 때
#   예) 의류: "의류|패션"
HSMOA_CATEGORY = os.environ.get("HSMOA_CATEGORY", "").strip()
HSMOA_CATEGORY_LOGO = os.environ.get("HSMOA_CATEGORY_LOGO", "").strip()

# (옵션) 후처리 키워드 필터: 타이틀에 키워드 포함 시만 남김 (정규식, | 구분)
HSMOA_CATEGORY_KEYWORDS = os.environ.get("HSMOA_CATEGORY_KEYWORDS", "").strip()

KST = datetime.timezone(datetime.timedelta(hours=9))
TODAY_TXT = datetime.datetime.now(KST).strftime("%Y-%m-%d")

BASE_URL = "https://hsmoa.com/"

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

async def click_first(page, locator):
    try:
        await locator.first.click(timeout=2000)
        return True
    except Exception:
        return False

async def click_by_label_like(page, pattern: str):
    """alt/title/aria-label 에 pattern(정규식) 들어간 첫 요소 클릭"""
    if not pattern:
        return False
    pat = re.compile(pattern, re.I)

    # 1) 이미지 alt
    for el in await page.locator("img[alt]").all():
        alt = await el.get_attribute("alt") or ""
        if pat.search(alt):
            try:
                await el.click(timeout=2000)
                return True
            except Exception:
                pass

    # 2) aria-label / title
    for el in await page.locator("[aria-label], [title]").all():
        label = (await el.get_attribute("aria-label")) or (await el.get_attribute("title")) or ""
        if pat.search(label):
            try:
                await el.click(timeout=2000)
                return True
            except Exception:
                pass
    return False

async def scrape_filtered_today():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(BASE_URL, wait_until="networkidle")

        # '오늘' 탭 보장 (있으면 클릭)
        await click_first(page, page.get_by_text("오늘"))

        # --- 홈쇼핑사 선택: 텍스트 → 로고 순서 ---
        if HSMOA_SHOP:
            ok = await click_first(page, page.get_by_text(HSMOA_SHOP))
            if not ok and HSMOA_SHOP_LOGO:
                await click_by_label_like(page, HSMOA_SHOP_LOGO)
        elif HSMOA_SHOP_LOGO:
            await click_by_label_like(page, HSMOA_SHOP_LOGO)

        # --- 카테고리 선택: 텍스트 → 로고 순서 ---
        if HSMOA_CATEGORY:
            ok = await click_first(page, page.get_by_text(HSMOA_CATEGORY))
            if not ok and HSMOA_CATEGORY_LOGO:
                await click_by_label_like(page, HSMOA_CATEGORY_LOGO)
        elif HSMOA_CATEGORY_LOGO:
            await click_by_label_like(page, HSMOA_CATEGORY_LOGO)

        # 로딩 안정화
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(800)

        # 항목 추출
        items = []
        rows = await page.locator("[data-testid='schedule-item']").all()
        if not rows:
            rows = await page.locator("li:has(.time), .schedule-item, .row:has(.time)").all()

        # 후처리 정규식
        logo_pat = re.compile(HSMOA_SHOP_LOGO, re.I) if HSMOA_SHOP_LOGO else None
        cat_kw_pat = re.compile(HSMOA_CATEGORY_KEYWORDS, re.I) if HSMOA_CATEGORY_KEYWORDS else None

        for r in rows[:200]:
            time_txt = clean(await r.locator(".time").inner_text()) if await r.locator(".time").count() else ""
            title_txt = clean(await r.locator(".title").inner_text()) if await r.locator(".title").count() else ""
            ch_txt = clean(await r.locator(".channel").inner_text()) if await r.locator(".channel").count() else ""
            price_txt = clean(await r.locator(".price").inner_text()) if await r.locator(".price").count() else ""

            # 홈쇼핑사 사후 필터 (채널 텍스트가 없으면 로고 패턴으로 보정)
            if HSMOA_SHOP and HSMOA_SHOP not in ch_txt:
                if not (logo_pat and logo_pat.search(title_txt)):
                    continue

            # 카테고리 사후 필터(옵션 키워드)
            if cat_kw_pat and not cat_kw_pat.search(title_txt):
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

def build_text(items):
    f = []
    if HSMOA_SHOP or HSMOA_SHOP_LOGO: f.append(HSMOA_SHOP or HSMOA_SHOP_LOGO)
    if HSMOA_CATEGORY or HSMOA_CATEGORY_LOGO: f.append(HSMOA_CATEGORY or HSMOA_CATEGORY_LOGO)
    filt_txt = " · ".join(f) if f else "전체"

    lines = [f"*{TODAY_TXT} 홈쇼핑모아 편성 – {filt_txt}*"]
    if not items:
        lines.append("_데이터 없음(필터/셀렉터 확인 필요)_")
    else:
        for e in items[:60]:
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

