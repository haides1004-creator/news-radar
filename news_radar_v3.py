from __future__ import annotations

import time
from datetime import datetime, timezone

import requests
from rapidfuzz import fuzz

import news_radar_v2 as base


def _is_naver_row(row: dict) -> bool:
    hay = " ".join([
        str(row.get("url", "")),
        str(row.get("domain", "")),
        str(row.get("source", "")),
        str(row.get("provider", "")),
    ]).casefold()
    return "naver.com" in hay or "네이버" in hay


def search_naver_keyword(
    keyword: str,
    start_date,
    end_date,
    client_id: str,
    client_secret: str,
) -> list[dict]:
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
        "User-Agent": base.USER_AGENT,
    }
    results: list[dict] = []

    for start_index in range(1, 1001, 100):
        params = {
            "query": keyword,
            "display": 100,
            "start": start_index,
            "sort": "date",
        }
        resp = requests.get(base.NAVER_NEWS_URL, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            break

        reached_old = False
        for item in items:
            published = base.parse_pubdate(item.get("pubDate", ""))
            if not published:
                continue
            if published.date() < start_date:
                reached_old = True
                continue
            if not base.in_range(published, start_date, end_date):
                continue

            # Prefer the Naver-hosted article URL when Naver supplies one.
            naver_url = (item.get("link") or "").strip()
            original_url = (item.get("originallink") or "").strip()
            url = naver_url or original_url
            if not url:
                continue

            original_domain = base.get_domain(original_url)
            url_domain = base.get_domain(url)
            is_naver = "naver.com" in url_domain
            if is_naver:
                source = f"네이버 뉴스 · {original_domain}" if original_domain else "네이버 뉴스"
            else:
                source = original_domain or url_domain or "네이버 뉴스 검색"

            results.append({
                "region": "한국",
                "title": base.strip_html(item.get("title")),
                "url": url,
                "normalized_url": base.normalize_url(url),
                "domain": url_domain,
                "source": source,
                "provider": "네이버 API",
                "published_at": published,
                "summary_seed": base.strip_html(item.get("description")),
                "matched_keywords": [keyword],
                "original_url": original_url,
            })

        if reached_old or len(items) < 100:
            break
        time.sleep(0.08)

    return results


def _search_naver_via_google(keyword: str, start_date, end_date) -> list[dict]:
    """Find Naver-hosted results even when the Naver API key is unavailable."""
    rows = base.search_google_rss_keyword(
        f"site:n.news.naver.com {keyword}", start_date, end_date, "한국"
    )
    for row in rows:
        row["provider"] = "네이버 전용 검색"
        old_source = row.get("source") or ""
        row["source"] = f"네이버 기사 · {old_source}" if old_source else "네이버 기사"
    return rows


def collect_korean_news(
    keywords: list[str],
    start_date,
    end_date,
    client_id: str,
    client_secret: str,
) -> tuple[list[dict], list[str], str]:
    rows: list[dict] = []
    errors: list[str] = []
    naver_ok = base.valid_naver_credentials(client_id, client_secret)

    for keyword in keywords:
        # 1) Naver API, when real credentials are present.
        if naver_ok:
            try:
                rows.extend(
                    search_naver_keyword(
                        keyword, start_date, end_date, client_id, client_secret
                    )
                )
            except Exception as exc:
                errors.append(f"네이버 API '{keyword}': {exc}")

        # 2) General Korean Google News feed, to keep broad coverage.
        try:
            general = base.search_google_rss_keyword(keyword, start_date, end_date, "한국")
            for row in general:
                row.setdefault("provider", "Google News RSS")
            rows.extend(general)
        except Exception as exc:
            errors.append(f"한국 일반 뉴스 '{keyword}': {exc}")

        # 3) Naver-hosted results through Google as an additional safety net.
        try:
            rows.extend(_search_naver_via_google(keyword, start_date, end_date))
        except Exception as exc:
            errors.append(f"네이버 기사 보강 '{keyword}': {exc}")

        time.sleep(0.12)

    if naver_ok:
        source_used = "네이버 API + Google News RSS + 네이버 전용 검색"
    else:
        source_used = "Google News RSS + 네이버 전용 검색 (실제 네이버 API 키 등록 시 네이버 API도 추가)"
    return rows, errors, source_used


def fuzzy_dedupe(rows: list[dict], threshold: int = 92) -> list[dict]:
    # For near-identical headlines, prefer a Naver-hosted/result row over the same
    # story's ordinary source so the user gets a Naver article link when possible.
    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    rows = sorted(
        rows,
        key=lambda x: (x.get("published_at") or min_dt, 1 if _is_naver_row(x) else 0),
        reverse=True,
    )

    kept: list[dict] = []
    normalized_kept: list[str] = []
    for row in rows:
        title = base.normalize_title(row.get("title", ""))
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
            prior = kept[duplicate_index]
            # If a Naver version appears for the same story, keep that version.
            if _is_naver_row(row) and not _is_naver_row(prior):
                merged_keywords = sorted(
                    set(prior.get("matched_keywords", []))
                    | set(row.get("matched_keywords", [])),
                    key=str.casefold,
                )
                row = row.copy()
                row["matched_keywords"] = merged_keywords
                kept[duplicate_index] = row
            else:
                prior["matched_keywords"] = sorted(
                    set(prior.get("matched_keywords", []))
                    | set(row.get("matched_keywords", [])),
                    key=str.casefold,
                )
    return kept


def prepare_results(
    rows: list[dict], include_terms: list[str], exclude_terms: list[str], limit: int = 20
) -> list[dict]:
    rows = [r for r in rows if base.publisher_allowed(r, include_terms, exclude_terms)]
    rows = base.merge_exact_urls(rows)
    rows = fuzzy_dedupe(rows)

    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    rows.sort(key=lambda x: x.get("published_at") or min_dt, reverse=True)

    # On Korean results, reserve room for Naver results when available so they
    # don't get crowded out by twenty other recent links.
    if rows and rows[0].get("region") == "한국":
        naver_rows = [r for r in rows if _is_naver_row(r)]
        others = [r for r in rows if not _is_naver_row(r)]
        reserve = min(8, len(naver_rows))
        chosen = naver_rows[:reserve]
        chosen_ids = {id(x) for x in chosen}
        for row in rows:
            if len(chosen) >= limit:
                break
            if id(row) not in chosen_ids:
                chosen.append(row)
                chosen_ids.add(id(row))
        chosen.sort(key=lambda x: x.get("published_at") or min_dt, reverse=True)
        return chosen[:limit]

    return rows[:limit]


# Patch v2's globals so its existing UI keeps working, with the improved sources.
base.search_naver_keyword = search_naver_keyword
base.collect_korean_news = collect_korean_news
base.fuzzy_dedupe = fuzzy_dedupe
base.prepare_results = prepare_results


def app() -> None:
    base.app()
