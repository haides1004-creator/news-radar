from __future__ import annotations

import html
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dt_time, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
import requests
import streamlit as st
import trafilatura
from dateutil import parser as date_parser
from rapidfuzz import fuzz


APP_TITLE = "뉴스 레이더"
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/140 Safari/537.36 NewsRadar/1.0"
)
TRACKING_PARAMS = {
    "gclid", "fbclid", "dclid", "mc_cid", "mc_eid", "ref", "ref_src",
    "igshid", "mkt_tok", "cmpid", "campaignid", "adgroupid",
}


# ----------------------------
# Text / URL helpers
# ----------------------------
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
        keyword = re.sub(r"\s+", " ", part).strip()
        key = keyword.casefold()
        if keyword and key not in seen:
            seen.add(key)
            out.append(keyword)
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


def compact(text: str, max_chars: int = 520) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 1].rsplit(" ", 1)[0]
    return (shortened or text[: max_chars - 1]) + "…"


# ----------------------------
# Date helpers
# ----------------------------
def parse_naver_date(value: str) -> datetime:
    return parsedate_to_datetime(value).astimezone(timezone.utc)


def parse_gdelt_date(value: str) -> datetime | None:
    if not value:
        return None
    candidates = [value, value.replace("T", "").replace("Z", "")]
    for candidate in candidates:
        try:
            if candidate.isdigit() and len(candidate) >= 14:
                return datetime.strptime(candidate[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            parsed = date_parser.parse(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            pass
    return None


def dt_in_range(dt: datetime, start: date, end: date) -> bool:
    # News providers can use different time zones. Date-level filtering is intentional.
    return start <= dt.date() <= end


# ----------------------------
# API clients
# ----------------------------
def search_naver_keyword(
    keyword: str,
    start_date: date,
    end_date: date,
    client_id: str,
    client_secret: str,
    max_pages: int = 10,
) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
        "User-Agent": USER_AGENT,
    }
    results: list[dict] = []

    for start_index in range(1, min(1000, max_pages * 100) + 1, 100):
        params = {
            "query": keyword,
            "display": 100,
            "start": start_index,
            "sort": "date",
        }
        resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            break

        reached_older_than_start = False
        for item in items:
            try:
                published_at = parse_naver_date(item.get("pubDate", ""))
            except Exception:
                continue

            if published_at.date() < start_date:
                reached_older_than_start = True
                continue
            if not dt_in_range(published_at, start_date, end_date):
                continue

            url = item.get("originallink") or item.get("link") or ""
            if not url:
                continue

            results.append(
                {
                    "region": "한국",
                    "title": strip_html(item.get("title")),
                    "url": url,
                    "normalized_url": normalize_url(url),
                    "domain": get_domain(url),
                    "source": get_domain(url),
                    "published_at": published_at,
                    "summary_seed": strip_html(item.get("description")),
                    "matched_keywords": [keyword],
                    "source_country": "South Korea",
                    "language": "Korean",
                }
            )

        # sort=date is descending, so once we are older than start_date there is no need to continue.
        if reached_older_than_start:
            break
        if len(items) < 100:
            break
        time.sleep(0.05)

    return results


def _gdelt_query(keyword: str) -> str:
    safe = keyword.replace('"', " ").strip()
    if " " in safe:
        safe = f'"{safe}"'
    # English-language sources make the overseas set more consistent and avoid most Korean outlets.
    return f"{safe} sourcelang:english"


def search_gdelt_keyword(keyword: str, start_date: date, end_date: date) -> list[dict]:
    start_dt = datetime.combine(start_date, dt_time.min).strftime("%Y%m%d%H%M%S")
    end_dt = datetime.combine(end_date, dt_time.max).strftime("%Y%m%d%H%M%S")
    params = {
        "query": _gdelt_query(keyword),
        "mode": "ArtList",
        "format": "json",
        "maxrecords": 250,
        "sort": "DateDesc",
        "startdatetime": start_dt,
        "enddatetime": end_dt,
    }
    resp = requests.get(
        GDELT_DOC_URL,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=25,
    )
    resp.raise_for_status()
    payload = resp.json()
    articles = payload.get("articles", []) if isinstance(payload, dict) else []
    results: list[dict] = []

    for item in articles:
        url = item.get("url") or item.get("url_mobile") or ""
        if not url:
            continue
        published_at = parse_gdelt_date(
            item.get("seendate") or item.get("date") or item.get("published") or ""
        )
        if published_at and not dt_in_range(published_at, start_date, end_date):
            continue

        source_country = strip_html(item.get("sourcecountry"))
        if source_country.casefold() in {
            "south korea", "korea, south", "republic of korea", "korea south", "ks"
        }:
            continue

        domain = strip_html(item.get("domain")) or get_domain(url)
        results.append(
            {
                "region": "해외",
                "title": strip_html(item.get("title")),
                "url": url,
                "normalized_url": normalize_url(url),
                "domain": domain.casefold(),
                "source": domain.casefold(),
                "published_at": published_at or datetime.combine(
                    start_date, dt_time.min, tzinfo=timezone.utc
                ),
                "summary_seed": strip_html(item.get("description") or item.get("desc")),
                "matched_keywords": [keyword],
                "source_country": source_country,
                "language": strip_html(item.get("language")) or "English",
            }
        )
    return results


def collect_korean_news(
    keywords: list[str], start_date: date, end_date: date, client_id: str, client_secret: str
) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    for keyword in keywords:
        try:
            rows.extend(
                search_naver_keyword(keyword, start_date, end_date, client_id, client_secret)
            )
        except Exception as exc:
            errors.append(f"한국 뉴스 '{keyword}': {exc}")
    return rows, errors


def collect_global_news(
    keywords: list[str], start_date: date, end_date: date
) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    for keyword in keywords:
        try:
            rows.extend(search_gdelt_keyword(keyword, start_date, end_date))
        except Exception as exc:
            errors.append(f"해외 뉴스 '{keyword}': {exc}")
        time.sleep(0.08)
    return rows, errors


# ----------------------------
# Filtering and deduplication
# ----------------------------
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
    # Newest article wins when near-identical headlines point to different URLs.
    rows = sorted(rows, key=lambda x: x.get("published_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    kept: list[dict] = []
    kept_titles: list[str] = []

    for row in rows:
        normalized = normalize_title(row.get("title", ""))
        if not normalized:
            continue

        duplicate_index = None
        for idx, prior in enumerate(kept_titles):
            if fuzz.token_set_ratio(normalized, prior) >= threshold:
                duplicate_index = idx
                break

        if duplicate_index is None:
            kept.append(row)
            kept_titles.append(normalized)
        else:
            kept[duplicate_index]["matched_keywords"] = sorted(
                set(kept[duplicate_index].get("matched_keywords", []))
                | set(row.get("matched_keywords", [])),
                key=str.casefold,
            )
    return kept


def prepare_results(
    rows: list[dict],
    include_terms: list[str],
    exclude_terms: list[str],
    limit: int = 20,
) -> list[dict]:
    rows = [r for r in rows if publisher_allowed(r, include_terms, exclude_terms)]
    rows = merge_exact_urls(rows)
    rows = fuzzy_dedupe(rows)
    rows.sort(
        key=lambda x: x.get("published_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return rows[:limit]


# ----------------------------
# Article extraction / summarization
# ----------------------------
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
        return compact(text or "", 8000)
    except Exception:
        return ""


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    # Works reasonably for Korean and Latin-script news without heavyweight NLP models.
    parts = re.split(r"(?<=[.!?。！？])\s+|(?<=다\.)\s*|(?<=요\.)\s*", text)
    return [p.strip() for p in parts if 25 <= len(p.strip()) <= 900]


def extractive_summary(text: str, keywords: Iterable[str], max_sentences: int = 3) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return compact(text, 520)

    keyword_terms = []
    for kw in keywords:
        keyword_terms.extend([t.casefold() for t in re.findall(r"[0-9A-Za-z가-힣]+", kw) if len(t) > 1])

    scored: list[tuple[float, int, str]] = []
    for idx, sentence in enumerate(sentences[:80]):
        low = sentence.casefold()
        keyword_score = sum(low.count(term) for term in keyword_terms)
        position_bonus = max(0.0, 2.0 - idx * 0.06)
        length_bonus = 0.5 if 50 <= len(sentence) <= 280 else 0.0
        scored.append((keyword_score * 4.0 + position_bonus + length_bonus, idx, sentence))

    selected = sorted(scored, key=lambda x: (-x[0], x[1]))[:max_sentences]
    selected = sorted(selected, key=lambda x: x[1])
    summary = " ".join(s for _, _, s in selected)
    return compact(summary, 620)


def summarize_one(article: dict, body_mode: bool) -> dict:
    row = article.copy()
    seed = row.get("summary_seed", "")
    text = ""

    if body_mode or not seed:
        text = fetch_article_text(row.get("url", ""))

    if text:
        row["summary"] = extractive_summary(text, row.get("matched_keywords", []))
        row["summary_source"] = "본문 추출"
    elif seed:
        row["summary"] = compact(seed, 620)
        row["summary_source"] = "검색 API 설명"
    else:
        row["summary"] = compact(row.get("title", ""), 620)
        row["summary_source"] = "제목 대체"
    return row


def summarize_articles(rows: list[dict], body_mode: bool) -> list[dict]:
    if not rows:
        return []
    output: list[dict | None] = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(rows)))) as executor:
        futures = {
            executor.submit(summarize_one, row, body_mode): idx for idx, row in enumerate(rows)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                output[idx] = future.result()
            except Exception:
                fallback = rows[idx].copy()
                fallback["summary"] = compact(fallback.get("summary_seed") or fallback.get("title", ""), 620)
                fallback["summary_source"] = "대체 요약"
                output[idx] = fallback
    return [x for x in output if x is not None]


# ----------------------------
# UI rendering
# ----------------------------
# ----------------------------
# UI rendering
# ----------------------------
def get_secret(name: str) -> str:
    """Read a Streamlit secret first, then fall back to an environment variable."""
    try:
        value = st.secrets.get(name, "")
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv(name, "")


def inject_mobile_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 860px;
            padding-top: 1.1rem;
            padding-bottom: 5rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }
        h1 { font-size: 1.9rem !important; margin-bottom: .25rem !important; }
        h2, h3 { line-height: 1.25 !important; }
        [data-testid="stMetricValue"] { font-size: 1.45rem; }
        [data-testid="stLinkButton"] a,
        [data-testid="stDownloadButton"] button,
        [data-testid="stBaseButton-primary"] {
            min-height: 46px;
            border-radius: 12px;
        }
        [data-testid="stTextInput"] input,
        [data-testid="stTextArea"] textarea,
        [data-testid="stDateInput"] input {
            font-size: 16px !important;
        }
        div[data-testid="stExpander"] details {
            border-radius: 14px;
        }
        @media (max-width: 640px) {
            .block-container { padding-left: .72rem; padding-right: .72rem; }
            h1 { font-size: 1.65rem !important; }
            h3 { font-size: 1.05rem !important; }
            [data-testid="column"] { min-width: 0 !important; }
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

    for idx, article in enumerate(rows, start=1):
        published = article.get("published_at")
        date_text = published.strftime("%Y-%m-%d") if published else "날짜 미상"
        source = article.get("source") or article.get("domain") or "출처 미상"
        keywords = ", ".join(article.get("matched_keywords", []))

        st.markdown(f"### {idx}. {article.get('title', '(제목 없음)')}")
        st.caption(f"{date_text} · {source}")
        st.write(article.get("summary") or "요약 없음")
        if keywords:
            st.caption(f"키워드: {keywords}")
        st.link_button(
            "원문 보기 ↗",
            article.get("url", "#"),
            use_container_width=True,
            key=f"{title}-{idx}-{article.get('normalized_url','')}",
        )
        st.divider()


def rows_to_dataframe(rows: list[dict]) -> pd.DataFrame:
    records = []
    for row in rows:
        published = row.get("published_at")
        records.append(
            {
                "구분": row.get("region"),
                "날짜_UTC": published.isoformat() if published else "",
                "언론사_도메인": row.get("source") or row.get("domain"),
                "제목": row.get("title"),
                "요약": row.get("summary"),
                "키워드": ", ".join(row.get("matched_keywords", [])),
                "링크": row.get("url"),
            }
        )
    return pd.DataFrame(records)


def app() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="📡",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    inject_mobile_css()

    st.title("📡 뉴스 레이더")
    st.caption("한국 20건 + 해외 20건 · 기간 필터 · 복수 키워드 · 중복 제거 · 기사 요약")

    today = date.today()
    default_start = today - timedelta(days=7)

    with st.container(border=True):
        st.markdown("#### 🔎 검색 조건")
        date_col1, date_col2 = st.columns(2)
        with date_col1:
            start_date = st.date_input("시작일", value=default_start)
        with date_col2:
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
            help="영문 키워드 권장. 줄바꿈 또는 쉼표로 여러 개 입력",
        )

        with st.expander("⚙️ 상세 옵션", expanded=False):
            st.caption("언론사/도메인 필터는 비워두면 전체입니다. 예: reuters.com, yna.co.kr")
            kr_include_raw = st.text_input("한국 포함 언론사/도메인", key="kr_include")
            kr_exclude_raw = st.text_input("한국 제외 언론사/도메인", key="kr_exclude")
            intl_include_raw = st.text_input("해외 포함 언론사/도메인", key="intl_include")
            intl_exclude_raw = st.text_input("해외 제외 언론사/도메인", key="intl_exclude")
            body_mode = st.checkbox(
                "기사 본문을 직접 읽어 요약",
                value=True,
                help="일부 사이트는 본문 접근을 막을 수 있습니다. 실패하면 검색 API 설명/제목으로 자동 대체합니다.",
            )

        default_client_id = get_secret("NAVER_CLIENT_ID")
        default_client_secret = get_secret("NAVER_CLIENT_SECRET")
        naver_client_id = default_client_id
        naver_client_secret = default_client_secret

        if not (naver_client_id and naver_client_secret):
            with st.expander("🔑 네이버 API 설정", expanded=True):
                st.warning("배포 전에는 Streamlit Secrets에 네이버 API 키를 넣는 것을 권장합니다.")
                naver_client_id = st.text_input("Client ID", type="password")
                naver_client_secret = st.text_input("Client Secret", type="password")

        search_clicked = st.button("🔎 뉴스 검색", type="primary", use_container_width=True)

    kr_keywords = parse_keywords(kr_raw)
    intl_keywords = parse_keywords(intl_raw)

    if search_clicked:
        if start_date > end_date:
            st.error("시작일이 종료일보다 늦습니다.")
            return
        if not kr_keywords and not intl_keywords:
            st.error("검색 키워드를 하나 이상 입력하세요.")
            return
        if kr_keywords and (not naver_client_id or not naver_client_secret):
            st.error("한국 뉴스 검색에는 네이버 Client ID와 Client Secret이 필요합니다.")
            return

        gdelt_earliest = date.today() - timedelta(days=92)
        intl_date_supported = start_date >= gdelt_earliest
        if intl_keywords and not intl_date_supported:
            st.warning(
                "해외 뉴스(GDELT)는 현재 앱에서 최근 약 3개월 범위만 검색합니다. "
                "선택한 시작일이 더 오래되어 해외 뉴스 수집은 건너뜁니다."
            )

        progress = st.progress(0, text="한국 뉴스 후보 수집 중…")
        kr_raw_rows, kr_errors = collect_korean_news(
            kr_keywords, start_date, end_date, naver_client_id, naver_client_secret
        ) if kr_keywords else ([], [])
        progress.progress(30, text=f"한국 후보 {len(kr_raw_rows)}건 · 해외 뉴스 후보 수집 중…")

        if intl_keywords and intl_date_supported:
            intl_raw_rows, intl_errors = collect_global_news(intl_keywords, start_date, end_date)
        else:
            intl_raw_rows, intl_errors = [], []

        progress.progress(60, text=f"해외 후보 {len(intl_raw_rows)}건 · 중복/언론사 필터 적용 중…")

        kr_results = prepare_results(
            kr_raw_rows,
            parse_filter_terms(kr_include_raw),
            parse_filter_terms(kr_exclude_raw),
            limit=20,
        )
        intl_results = prepare_results(
            intl_raw_rows,
            parse_filter_terms(intl_include_raw),
            parse_filter_terms(intl_exclude_raw),
            limit=20,
        )

        progress.progress(75, text="기사 요약 생성 중…")
        kr_results = summarize_articles(kr_results, body_mode=body_mode)
        intl_results = summarize_articles(intl_results, body_mode=body_mode)
        progress.progress(100, text="완료")
        time.sleep(0.1)
        progress.empty()

        st.session_state["kr_results"] = kr_results
        st.session_state["intl_results"] = intl_results
        st.session_state["search_meta"] = {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "kr_candidates": len(kr_raw_rows),
            "intl_candidates": len(intl_raw_rows),
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
            f"기간 {meta.get('start')} ~ {meta.get('end')} · "
            f"후보 {meta.get('kr_candidates', 0) + meta.get('intl_candidates', 0)}건"
        )

        errors = meta.get("errors", [])
        if errors:
            with st.expander(f"일부 검색 오류 {len(errors)}건"):
                for err in errors:
                    st.write(f"• {err}")

        combined_df = rows_to_dataframe(kr_results + intl_results)
        st.download_button(
            "⬇️ 결과 CSV 저장",
            data=combined_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"news_radar_{meta.get('start')}_{meta.get('end')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

        tab_kr, tab_intl = st.tabs([
            f"🇰🇷 한국 {len(kr_results)}",
            f"🌍 해외 {len(intl_results)}",
        ])
        with tab_kr:
            render_articles("한국 뉴스", kr_results)
        with tab_intl:
            render_articles("해외 뉴스", intl_results)
    else:
        st.info("기간과 키워드를 설정하고 **뉴스 검색**을 눌러주세요.")

    with st.expander("ℹ️ 앱 사용 팁 / 제한 사항"):
        st.markdown(
            """
- **한국 뉴스:** 네이버 뉴스 검색 API를 이용합니다.
- **해외 뉴스:** GDELT DOC 2.0에서 영어권 기사 위주로 수집합니다.
- **중복 제거:** 추적 파라미터를 제거한 URL과 유사 제목을 함께 검사합니다.
- **기사 요약:** 유료 AI API 없이 기사 본문에서 핵심 문장을 추출하며, 본문 접근 실패 시 검색 설명 또는 제목으로 대체합니다.
- **홈 화면 사용:** 배포 후 휴대폰 브라우저의 **홈 화면에 추가** 기능을 쓰면 아이콘으로 바로 열 수 있습니다.
            """
        )


if __name__ == "__main__":
    app()
