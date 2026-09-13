from __future__ import annotations

import html
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
import requests
import streamlit as st
import trafilatura
from rapidfuzz import fuzz

APP_TITLE = "뉴스 레이더"
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/140 Safari/537.36 NewsRadar/2.0"
)
TRACKING_PARAMS = {
    "gclid", "fbclid", "dclid", "mc_cid", "mc_eid", "ref", "ref_src",
    "igshid", "mkt_tok", "cmpid", "campaignid", "adgroupid",
}


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_keywords(raw: str) -> list[str]:
    parts = re.split(r"[\n,;]+", raw or "")
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        item = re.sub(r"\s+", " ", part).strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def parse_filter_terms(raw: str) -> list[str]:
    return [x.casefold() for x in parse_keywords(raw)]


def normalize_url(url: str) -> str:
    try:
        split = urlsplit(url.strip())
        query = []
        for key, value in parse_qsl(split.query, keep_blank_values=True):
            low = key.casefold()
            if low.startswith("utm_") or low in TRACKING_PARAMS:
                continue
            query.append((key, value))
        path = re.sub(r"/{2,}", "/", split.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit(
            (split.scheme.casefold(), split.netloc.casefold(), path, urlencode(query), "")
        )
    except Exception:
        return url.strip()


def get_domain(url: str) -> str:
    try:
        host = urlsplit(url).netloc.casefold()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def normalize_title(title: str) -> str:
    title = strip_html(title).casefold()
    title = re.sub(r"\[[^\]]+\]", " ", title)
    title = re.sub(r"\([^\)]{0,40}\)", " ", title)
    title = re.sub(r"[^0-9a-z가-힣]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def compact(text: str, max_chars: int = 620) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1].rsplit(" ", 1)[0]
    return (cut or text[: max_chars - 1]) + "…"


def parse_pubdate(value: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def in_range(dt: datetime, start: date, end: date) -> bool:
    return start <= dt.date() <= end


def get_secret(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
        if value:
            return str(value).strip()
    except Exception:
        pass
    return os.getenv(name, "").strip()


def valid_naver_credentials(client_id: str, client_secret: str) -> bool:
    """Reject blank/example/non-ASCII strings before they reach HTTP headers."""
    client_id = (client_id or "").strip()
    client_secret = (client_secret or "").strip()
    if not client_id or not client_secret:
        return False
    if not client_id.isascii() or not client_secret.isascii():
        return False
    joined = f"{client_id} {client_secret}".casefold()
    placeholders = ("client_id", "client_secret", "your_", "네이버", "여기에", "입력")
    return not any(token.casefold() in joined for token in placeholders)


def search_naver_keyword(
    keyword: str,
    start_date: date,
    end_date: date,
    client_id: str,
    client_secret: str,
) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
        "User-Agent": USER_AGENT,
    }
    results: list[dict] = []

    for start_index in range(1, 1001, 100):
        params = {"query": keyword, "display": 100, "start": start_index, "sort": "date"}
        resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            break

        reached_old = False
        for item in items:
            published = parse_pubdate(item.get("pubDate", ""))
            if not published:
                continue
            if published.date() < start_date:
                reached_old = True
                continue
            if not in_range(published, start_date, end_date):
                continue
            url = item.get("originallink") or item.get("link") or ""
            if not url:
                continue
            domain = get_domain(url)
            results.append({
                "region": "한국",
                "title": strip_html(item.get("title")),
                "url": url,
                "normalized_url": normalize_url(url),
                "domain": domain,
                "source": domain,
                "published_at": published,
                "summary_seed": strip_html(item.get("description")),
                "matched_keywords": [keyword],
            })

        if reached_old or len(items) < 100:
            break
        time.sleep(0.08)

    return results


def search_google_rss_keyword(
    keyword: str,
    start_date: date,
    end_date: date,
    region: str,
) -> list[dict]:
    before_date = end_date + timedelta(days=1)
    query = f"{keyword} after:{start_date.isoformat()} before:{before_date.isoformat()}"
    if region == "한국":
        params = {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    else:
        params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}

    resp = requests.get(
        GOOGLE_NEWS_RSS_URL,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    results: list[dict] = []

    for item in root.findall("./channel/item"):
        title = strip_html(item.findtext("title") or "")
        url = (item.findtext("link") or "").strip()
        published = parse_pubdate(item.findtext("pubDate") or "")
        if not title or not url or not published or not in_range(published, start_date, end_date):
            continue

        source_el = item.find("source")
        source_name = strip_html(source_el.text if source_el is not None else "")
        source_url = source_el.attrib.get("url", "") if source_el is not None else ""
        source_domain = get_domain(source_url)
        source = source_domain or source_name or get_domain(url)

        results.append({
            "region": region,
            "title": title,
            "url": url,
            "normalized_url": normalize_url(url),
            "domain": source_domain or get_domain(url),
            "source": source,
            "published_at": published,
            "summary_seed": strip_html(item.findtext("description") or ""),
            "matched_keywords": [keyword],
        })
    return results


def collect_korean_news(
    keywords: list[str],
    start_date: date,
    end_date: date,
    client_id: str,
    client_secret: str,
) -> tuple[list[dict], list[str], str]:
    rows: list[dict] = []
    errors: list[str] = []
    naver_ok = valid_naver_credentials(client_id, client_secret)
    source_used = "네이버 뉴스 API" if naver_ok else "Google News RSS"

    for keyword in keywords:
        if naver_ok:
            try:
                found = search_naver_keyword(keyword, start_date, end_date, client_id, client_secret)
                rows.extend(found)
                continue
            except Exception:
                source_used = "네이버 실패 → Google News RSS 대체"

        try:
            rows.extend(search_google_rss_keyword(keyword, start_date, end_date, "한국"))
        except Exception as exc:
            errors.append(f"한국 뉴스 '{keyword}': {exc}")
        time.sleep(0.15)

    return rows, errors, source_used


def collect_global_news(
    keywords: list[str], start_date: date, end_date: date
) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    for keyword in keywords:
        try:
            rows.extend(search_google_rss_keyword(keyword, start_date, end_date, "해외"))
        except Exception as exc:
            errors.append(f"해외 뉴스 '{keyword}': {exc}")
        time.sleep(0.15)
    return rows, errors


def publisher_allowed(article: dict, include: list[str], exclude: list[str]) -> bool:
    haystack = f"{article.get('source', '')} {article.get('domain', '')}".casefold()
    if include and not any(term in haystack for term in include):
        return False
    if exclude and any(term in haystack for term in exclude):
        return False
    return True


def merge_exact_urls(rows: list[dict]) -> list[dict]:
    by_url: dict[str, dict] = {}
    for row in rows:
        key = row.get("normalized_url") or normalize_url(row.get("url", ""))
        if key not in by_url:
            by_url[key] = row.copy()
            continue
        existing = by_url[key]
        existing["matched_keywords"] = sorted(
            set(existing.get("matched_keywords", [])) | set(row.get("matched_keywords", [])),
            key=str.casefold,
        )
        if len(row.get("summary_seed", "")) > len(existing.get("summary_seed", "")):
            existing["summary_seed"] = row.get("summary_seed", "")
    return list(by_url.values())


def fuzzy_dedupe(rows: list[dict], threshold: int = 92) -> list[dict]:
    rows = sorted(rows, key=lambda x: x["published_at"], reverse=True)
    kept: list[dict] = []
    normalized_kept: list[str] = []
    for row in rows:
        title = normalize_title(row.get("title", ""))
        if not title:
            continue
        duplicate_index = None
        for idx, old_title in enumerate(normalized_kept):
            if fuzz.token_set_ratio(title, old_title) >= threshold:
                duplicate_index = idx
                break
        if duplicate_index is None:
            kept.append(row)
            normalized_kept.append(title)
        else:
            kept[duplicate_index]["matched_keywords"] = sorted(
                set(kept[duplicate_index].get("matched_keywords", []))
                | set(row.get("matched_keywords", [])),
                key=str.casefold,
            )
    return kept


def prepare_results(
    rows: list[dict], include_terms: list[str], exclude_terms: list[str], limit: int = 20
) -> list[dict]:
    rows = [r for r in rows if publisher_allowed(r, include_terms, exclude_terms)]
    rows = merge_exact_urls(rows)
    rows = fuzzy_dedupe(rows)
    rows.sort(key=lambda x: x["published_at"], reverse=True)
    return rows[:limit]


def fetch_article_text(url: str) -> str:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        resp.raise_for_status()
        text = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            no_fallback=False,
        )
        return compact(text or "", 9000)
    except Exception:
        return ""


def extractive_summary(text: str, keywords: list[str], max_sentences: int = 3) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    sentences = [
        p.strip()
        for p in re.split(r"(?<=[.!?。！？])\s+|(?<=다\.)\s*|(?<=요\.)\s*", text)
        if 25 <= len(p.strip()) <= 900
    ]
    if not sentences:
        return compact(text)

    terms: list[str] = []
    for keyword in keywords:
        terms.extend(t.casefold() for t in re.findall(r"[0-9A-Za-z가-힣]+", keyword) if len(t) > 1)

    scored: list[tuple[float, int, str]] = []
    for idx, sentence in enumerate(sentences[:80]):
        low = sentence.casefold()
        keyword_score = sum(low.count(term) for term in terms)
        position_bonus = max(0.0, 2.0 - idx * 0.06)
        length_bonus = 0.5 if 50 <= len(sentence) <= 280 else 0.0
        scored.append((keyword_score * 4 + position_bonus + length_bonus, idx, sentence))

    selected = sorted(scored, key=lambda x: (-x[0], x[1]))[:max_sentences]
    selected.sort(key=lambda x: x[1])
    return compact(" ".join(item[2] for item in selected))


def summarize_one(article: dict, body_mode: bool) -> dict:
    row = article.copy()
    seed = row.get("summary_seed", "")
    body = fetch_article_text(row.get("url", "")) if body_mode or not seed else ""
    if body:
        row["summary"] = extractive_summary(body, row.get("matched_keywords", []))
    elif seed:
        row["summary"] = compact(seed)
    else:
        row["summary"] = compact(row.get("title", ""))
    return row


def summarize_articles(rows: list[dict], body_mode: bool) -> list[dict]:
    if not rows:
        return []
    output: list[dict | None] = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=min(6, len(rows))) as executor:
        futures = {executor.submit(summarize_one, row, body_mode): idx for idx, row in enumerate(rows)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                output[idx] = future.result()
            except Exception:
                fallback = rows[idx].copy()
                fallback["summary"] = compact(fallback.get("summary_seed") or fallback.get("title", ""))
                output[idx] = fallback
    return [row for row in output if row is not None]


def inject_mobile_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {max-width: 860px; padding: 1.1rem 1rem 5rem;}
        h1 {font-size:1.9rem !important; margin-bottom:.25rem !important;}
        [data-testid="stMetricValue"] {font-size:1.45rem;}
        [data-testid="stLinkButton"] a,
        [data-testid="stDownloadButton"] button,
        [data-testid="stBaseButton-primary"] {min-height:46px; border-radius:12px;}
        [data-testid="stTextInput"] input,
        [data-testid="stTextArea"] textarea,
        [data-testid="stDateInput"] input {font-size:16px !important;}
        div[data-testid="stExpander"] details {border-radius:14px;}
        @media (max-width:640px) {
            .block-container {padding-left:.72rem; padding-right:.72rem;}
            h1 {font-size:1.65rem !important;}
            h3 {font-size:1.05rem !important;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_articles(title: str, rows: list[dict]) -> None:
    st.subheader(f"{title} · {len(rows)}건")
    if not rows:
        st.info("조건에 맞는 기사가 없습니다. 기간, 키워드 또는 언론사 필터를 넓혀보세요.")
        return
    for idx, article in enumerate(rows, 1):
        st.markdown(f"### {idx}. {article.get('title', '(제목 없음)')}")
        st.caption(
            f"{article['published_at'].strftime('%Y-%m-%d')} · "
            f"{article.get('source') or article.get('domain') or '출처 미상'}"
        )
        st.write(article.get("summary") or "요약 없음")
        keywords = ", ".join(article.get("matched_keywords", []))
        if keywords:
            st.caption(f"키워드: {keywords}")
        st.link_button(
            "원문 보기 ↗",
            article.get("url", "#"),
            use_container_width=True,
            key=f"{title}-{idx}-{article.get('normalized_url', '')}",
        )
        st.divider()


def rows_to_dataframe(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "구분": row.get("region"),
            "날짜_UTC": row["published_at"].isoformat(),
            "언론사_도메인": row.get("source") or row.get("domain"),
            "제목": row.get("title"),
            "요약": row.get("summary"),
            "키워드": ", ".join(row.get("matched_keywords", [])),
            "링크": row.get("url"),
        }
        for row in rows
    ])


def app() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📡", layout="centered", initial_sidebar_state="collapsed")
    inject_mobile_css()
    st.title("📡 뉴스 레이더")
    st.caption("한국 20건 + 해외 20건 · 기간 필터 · 복수 키워드 · 중복 제거 · 기사 요약")

    today = date.today()
    with st.container(border=True):
        st.markdown("#### 🔎 검색 조건")
        c1, c2 = st.columns(2)
        with c1:
            start_date = st.date_input("시작일", value=today - timedelta(days=7))
        with c2:
            end_date = st.date_input("종료일", value=today)

        kr_raw = st.text_area(
            "🇰🇷 한국 뉴스 키워드",
            value="신장 재생\n인공신장\n이종이식",
            height=105,
            help="줄바꿈 또는 쉼표로 여러 개 입력",
        )
        intl_raw = st.text_area(
            "🌍 해외 뉴스 키워드",
            value="kidney regeneration\nbioartificial kidney\nxenotransplantation",
            height=105,
            help="영문 키워드 권장",
        )

        with st.expander("⚙️ 상세 옵션"):
            st.caption("언론사/도메인 필터는 비워두면 전체입니다. 예: reuters.com, yna.co.kr")
            kr_include_raw = st.text_input("한국 포함 언론사/도메인")
            kr_exclude_raw = st.text_input("한국 제외 언론사/도메인")
            intl_include_raw = st.text_input("해외 포함 언론사/도메인")
            intl_exclude_raw = st.text_input("해외 제외 언론사/도메인")
            body_mode = st.checkbox(
                "기사 본문을 직접 읽어 요약",
                value=True,
                help="접근이 막힌 사이트는 RSS 설명/제목으로 자동 대체합니다.",
            )

        client_id = get_secret("NAVER_CLIENT_ID")
        client_secret = get_secret("NAVER_CLIENT_SECRET")
        if not valid_naver_credentials(client_id, client_secret):
            st.caption("ℹ️ 네이버 API 키가 없거나 예시값이면 한국 뉴스는 Google News RSS로 자동 검색합니다.")

        search_clicked = st.button("🔎 뉴스 검색", type="primary", use_container_width=True)

    if search_clicked:
        kr_keywords = parse_keywords(kr_raw)
        intl_keywords = parse_keywords(intl_raw)
        if start_date > end_date:
            st.error("시작일이 종료일보다 늦습니다.")
            return
        if not kr_keywords and not intl_keywords:
            st.error("검색 키워드를 하나 이상 입력하세요.")
            return

        progress = st.progress(0, text="한국 뉴스 후보 수집 중…")
        kr_rows, kr_errors, kr_source = collect_korean_news(
            kr_keywords, start_date, end_date, client_id, client_secret
        ) if kr_keywords else ([], [], "사용 안 함")
        progress.progress(35, text=f"한국 후보 {len(kr_rows)}건 · 해외 뉴스 후보 수집 중…")

        intl_rows, intl_errors = collect_global_news(
            intl_keywords, start_date, end_date
        ) if intl_keywords else ([], [])
        progress.progress(65, text=f"해외 후보 {len(intl_rows)}건 · 중복 제거 중…")

        kr_results = prepare_results(
            kr_rows, parse_filter_terms(kr_include_raw), parse_filter_terms(kr_exclude_raw), 20
        )
        intl_results = prepare_results(
            intl_rows, parse_filter_terms(intl_include_raw), parse_filter_terms(intl_exclude_raw), 20
        )

        progress.progress(78, text="기사 요약 생성 중…")
        kr_results = summarize_articles(kr_results, body_mode)
        intl_results = summarize_articles(intl_results, body_mode)
        progress.progress(100, text="완료")
        time.sleep(0.1)
        progress.empty()

        st.session_state["kr_results"] = kr_results
        st.session_state["intl_results"] = intl_results
        st.session_state["search_meta"] = {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "kr_candidates": len(kr_rows),
            "intl_candidates": len(intl_rows),
            "kr_source": kr_source,
            "errors": kr_errors + intl_errors,
        }

    kr_results = st.session_state.get("kr_results", [])
    intl_results = st.session_state.get("intl_results", [])
    meta = st.session_state.get("search_meta")

    if meta:
        st.markdown("### 검색 결과")
        a, b = st.columns(2)
        a.metric("🇰🇷 한국", len(kr_results))
        b.metric("🌍 해외", len(intl_results))
        st.caption(
            f"기간 {meta['start']} ~ {meta['end']} · 후보 "
            f"{meta['kr_candidates'] + meta['intl_candidates']}건 · 한국 수집원: {meta['kr_source']}"
        )
        errors = meta.get("errors", [])
        if errors:
            with st.expander(f"일부 검색 오류 {len(errors)}건"):
                for err in errors:
                    st.write(f"• {err}")

        df = rows_to_dataframe(kr_results + intl_results)
        st.download_button(
            "⬇️ 결과 CSV 저장",
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"news_radar_{meta['start']}_{meta['end']}.csv",
            mime="text/csv",
            use_container_width=True,
        )

        tab_kr, tab_intl = st.tabs([f"🇰🇷 한국 {len(kr_results)}", f"🌍 해외 {len(intl_results)}"])
        with tab_kr:
            render_articles("한국 뉴스", kr_results)
        with tab_intl:
            render_articles("해외 뉴스", intl_results)
    else:
        st.info("기간과 키워드를 설정하고 **뉴스 검색**을 눌러주세요.")

    with st.expander("ℹ️ 앱 사용 팁 / 제한 사항"):
        st.markdown(
            """
- **한국 뉴스:** 유효한 네이버 API 키가 있으면 네이버를 우선 사용하고, 없거나 실패하면 Google News RSS로 자동 전환합니다.
- **해외 뉴스:** Google News RSS의 날짜 검색을 사용합니다.
- **중복 제거:** URL 추적 파라미터와 유사 제목을 함께 검사합니다.
- **기사 요약:** 본문 접근이 가능하면 핵심 문장을 추출하고, 실패하면 RSS/API 설명 또는 제목으로 대체합니다.
- **홈 화면 사용:** 휴대폰 브라우저에서 **홈 화면에 추가**를 선택하면 앱 아이콘처럼 사용할 수 있습니다.
            """
        )


if __name__ == "__main__":
    app()
