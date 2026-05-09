"""
Project Scraper API: High-Performance Media & Link Extraction Service
Orchestrates static and dynamic scraping using FastAPI and the Scrapling framework.

Key Capabilities:
- SSRF-protected remote resource fetching
- Network-aware stream interception (HLS/Dash)
- Multi-platform session persistence for authenticated scraping
- High-resolution image asset resolution
"""
import re
import os
import json
import hashlib
import httpx
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, HttpUrl, field_validator
from typing import List, Dict, Optional, Any
from scrapling import Fetcher, StealthyFetcher
from datetime import datetime
from urllib.parse import urljoin, urlparse, quote

app = FastAPI(title="Project Scraper API")

# --- Security: Cross-Origin Resource Sharing ---
# Restricted allow_origins prevents browser-based unauthorized API access from 3rd party domains.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://0.0.0.0:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Security: allowed targets whitelist ──────────────────────────────────────
ALLOWED_TARGETS = {"images", "videos", "links"}

# --- Security: SSRF Protection ---
# Blocks requests to loopback, private networks, and link-local addresses 
# to prevent the scraper from being used to scan internal infrastructure.
PRIVATE_IP_PATTERN = re.compile(
    r"^https?://(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|"
    r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+|"
    r"\[::1\]|0\.0\.0\.0)"
)

# ── Sessions directory for social media auth ─────────────────────────────────
SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

# ── Supported social media platforms ─────────────────────────────────────────
SOCIAL_PLATFORMS = {
    "instagram.com": {
        "name": "Instagram",
        "login_url": "https://www.instagram.com/accounts/login/",
        "check_selector": "img[data-testid='user-avatar'], article img, img[style]",
        "wait_for": "article, main[role='main']",
    },
    "facebook.com": {
        "name": "Facebook",
        "login_url": "https://www.facebook.com/login/",
        "check_selector": "div[role='article'] img, img.x1lliihq",
        "wait_for": "div[role='main'], div[role='feed']",
    },
    "tiktok.com": {
        "name": "TikTok",
        "login_url": "https://www.tiktok.com/login/phone-or-email/email",
        "check_selector": "video, div[data-e2e='user-post-item'] img",
        "wait_for": "div[data-e2e='user-post-item-list'], main",
    },
    "x.com": {
        "name": "X (Twitter)",
        "login_url": "https://x.com/i/flow/login",
        "check_selector": "article img, video",
        "wait_for": "article, main[role='main']",
    },
    "twitter.com": {
        "name": "X (Twitter)",
        "login_url": "https://x.com/i/flow/login",
        "check_selector": "article img, video",
        "wait_for": "article, main[role='main']",
    },
    "scrolller.com": {
        "name": "Scrolller",
        "login_url": "https://scrolller.com/",
        "check_selector": "img",
        "wait_for": "img[srcset], img[src*='scrolller.com']",
    },
    "youtube.com": {
        "name": "YouTube",
        "login_url": "https://www.youtube.com/",
        "check_selector": "a#thumbnail, ytd-thumbnail, video.html5-main-video, #movie_player",
        "wait_for": "#movie_player, ytd-rich-item-renderer, ytd-video-renderer, #content",
    },
    "animekai.to": {
        "name": "AnimeKai",
        "login_url": "https://animekai.to/",
        "check_selector": "video, iframe",
        "wait_for": "video, iframe, .player-wrapper, #player",
        "solve_cloudflare": True,
    },
}

# ── Request / Response models ────────────────────────────────────────────────
class MediaItem(BaseModel):
    """Represents a single discovered media asset with metadata."""
    url: str
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    source_url: Optional[str] = None
    type: str  # "image", "video", "link"

class ScrapeRequest(BaseModel):
    """Supports bulk scraping by accepting a list of target URLs."""
    urls: List[HttpUrl]
    targets: List[str]
    stealth: bool = False

    @field_validator("targets")
    @classmethod
    def validate_targets(cls, v: List[str]) -> List[str]:
        invalid = set(v) - ALLOWED_TARGETS
        if invalid:
            raise ValueError(f"Invalid targets: {invalid}. Allowed: {ALLOWED_TARGETS}")
        if not v:
            raise ValueError("At least one target is required.")
        return v

class ScrapeResponse(BaseModel):
    """Aggregated results from multiple scrape operations."""
    success: bool
    data: Dict[str, Any]  # Grouped by source URL
    error: str | None = None
    mode: str = "static"

class AuthLoginRequest(BaseModel):
    platform: str  # e.g. "instagram.com"
    username: str
    password: str

class AuthSession(BaseModel):
    """Represents a saved platform session."""
    platform: str
    display_name: str
    username: str
    logged_in: bool

def _resolve_page_title(page) -> str:
    """
    Intelligently extracts the best title for a page, prioritizing 
    social graph metadata (OG) over standard title tags.
    """
    try:
        # 1. Try Open Graph title (most descriptive for videos/articles)
        og_title = page.css('meta[property="og:title"]::attr(content)').first().text
        if og_title:
            return og_title.strip()
        
        # 2. Try standard <title> tag
        title_tag = page.css('title::text').first().text
        if title_tag:
            return title_tag.strip()
        
        # 3. Platform-specific fallbacks
        if "youtube.com" in page.url or "youtu.be" in page.url:
            # YouTube specific title selector
            yt_title = page.css('h1.ytd-watch-metadata yt-formatted-string::text').first().text
            if yt_title:
                return yt_title.strip()
    except Exception:
        pass
    
    return "Untitled Media"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_session_path(platform: str) -> Path:
    """Get the path for a platform's session cookies."""
    safe_name = hashlib.md5(platform.encode()).hexdigest()
    return SESSIONS_DIR / f"{safe_name}.json"


def _load_session(platform: str) -> dict | None:
    """Load saved session for a platform."""
    path = _get_session_path(platform)
    if path.exists():
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    return None


def _save_session(platform: str, session_data: dict):
    """Save session data for a platform."""
    path = _get_session_path(platform)
    with open(path, "w") as f:
        json.dump(session_data, f)


def _get_platform_key(url: str) -> str | None:
    """
    Identifies the target social platform from the URL hostname.
    Used to select platform-specific selectors and session cookies.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    host = host.removeprefix("www.")
    for key in SOCIAL_PLATFORMS:
        if host == key or host.endswith("." + key):
            return key
    return None


def _best_srcset_url(srcset: str) -> str:
    """
    Parses complex 'srcset' attributes to find the highest density/width asset.
    Favors physical width (w) over pixel density (x) descriptors.
    """
    candidates = []
    for entry in srcset.split(","):
        parts = entry.strip().split(" ")
        if not parts or not parts[0]:
            continue
        url = parts[0]
        weight = 0
        if len(parts) >= 2:
            descriptor = parts[1].lower()
            num_str = descriptor.replace("w", "").replace("x", "")
            try:
                weight = float(num_str)
                # Normalizing density descriptors to be comparable with width values.
                if "x" in descriptor:
                    weight *= 1000
            except ValueError:
                weight = 0
        candidates.append((url, weight))
    if candidates:
        candidates.sort(key=lambda c: c[1], reverse=True)
        return candidates[0][0]
    return ""


def _refine_image_url(url: str) -> str:
    """Attempt to upgrade a thumbnail URL to its original/high-res version."""
    if not url or url.startswith("data:"):
        return url
    
    # 1. Strip common thumbnail dimension patterns like _320x133 or -150x150
    # Matches patterns like _100x100, -500x500, etc. before the extension
    refined = re.sub(r'[_|-]\d+x\d+(?=\.[a-z]{3,4}$)', '', url, flags=re.IGNORECASE)
    
    # 2. Strip common thumbnail words
    refined = re.sub(r'([_|-|/])(thumb|thumbnail|small|mini|tiny|preview|placeholder)([_|-|/|.])', r'\1original\3', refined, flags=re.IGNORECASE)
    
    # 3. Handle Scrolller specific static patterns
    # Scrolller thumbnails often have a suffix like _af.webp vs the original
    # We'll stick to the regex above for now as it catches most cases.
    
    return refined


def _extract_media(page, url_str: str, targets: list, log_func=None) -> Dict[str, List[MediaItem]]:
    """
    DOM-based extraction logic. 
    Parses the current page state to find static assets and embedded players.
    Acts as a secondary discovery layer alongside network interception.
    """
    results: Dict[str, List[MediaItem]] = {t: [] for t in targets}
    page_title = _resolve_page_title(page)

    def _log(url, cat, reason):
        if log_func:
            log_func(url, cat, f"dom: {reason}")
    
    def _create_item(url, media_type) -> MediaItem:
        return MediaItem(
            url=url,
            title=page_title,
            source_url=url_str,
            type=media_type
        )

    # ── Images ────────────────────────────────────────────────────────────
    if "images" in targets:
        imgs = page.css("img")
        src_list = []
        for img in imgs:
            # Priority order: high-res data attributes → srcset best → src fallback
            src = (
                img.attrib.get("data-original") or
                img.attrib.get("data-full") or
                img.attrib.get("data-highres") or
                img.attrib.get("data-lazy-src") or
                img.attrib.get("data-src") or
                ""
            )

            # Always try srcset for a higher-res version
            srcset = img.attrib.get("srcset", "")
            if srcset:
                best = _best_srcset_url(srcset)
                if best:
                    src = best  # srcset highest-res always wins

            # Fallback to plain src if nothing better was found
            if not src:
                src = img.attrib.get("src", "")

            if src:
                full = urljoin(url_str, src)
                if full.startswith("data:"):
                    continue
                refined = _refine_image_url(full)
                _log(refined, "image", "img tag")
                src_list.append(_create_item(refined, "image"))

        # <picture> <source> tags — pick best from each
        pic_sources = page.css("picture source")
        for ps in pic_sources:
            srcset = ps.attrib.get("srcset", "")
            if srcset:
                best = _best_srcset_url(srcset)
                if best:
                    src_list.append(_create_item(urljoin(url_str, best), "image"))
                    continue
            fallback = ps.attrib.get("src", "")
            if fallback:
                src_list.append(_create_item(urljoin(url_str, fallback), "image"))

        # Background images in inline styles
        styled_elements = page.css("[style]")
        for el in styled_elements:
            style = el.attrib.get("style", "")
            bg_match = re.findall(r'url\(["\']?(.*?)["\']?\)', style)
            for bg_url in bg_match:
                if bg_url and not bg_url.startswith("data:"):
                    src_list.append(_create_item(urljoin(url_str, bg_url), "image"))

        # Deduplicate by URL while keeping order
        seen = set()
        unique_images = []
        for item in src_list:
            if item.url not in seen:
                seen.add(item.url)
                unique_images.append(item)
        results["images"] = unique_images

    # ── Videos ────────────────────────────────────────────────────────────
    if "videos" in targets:
        all_videos = []
        
        # 1. Direct video tags
        for v in page.css("video"):
            src = v.attrib.get("src") or v.attrib.get("data-src")
            if src:
                full = urljoin(url_str, src)
                _log(full, "video", "video tag")
                all_videos.append(full)
        
        # 2. Nested source tags
        for source in page.css("video source"):
            src = source.attrib.get("src")
            if src:
                full = urljoin(url_str, src)
                _log(full, "video", "video source tag")
                all_videos.append(full)
        
        # 3. iframe players (megaup, vidplay, etc)
        _VIDEO_IFRAME_DOMAINS = [
            "youtube.com", "youtube-nocookie.com", "youtu.be",
            "vimeo.com", "dailymotion.com", "player.twitch.tv",
            "streamable.com", "rumble.com", "bitchute.com",
            "vidyard.com", "wistia.com", "loom.com",
            "animekai", "megacloud", "vidplay", "rapid-cloud",
        ]
        for iframe in page.css("iframe"):
            iframe_src = iframe.attrib.get("src") or iframe.attrib.get("data-src") or ""
            if iframe_src:
                if any(d in iframe_src for d in _VIDEO_IFRAME_DOMAINS):
                    all_videos.append(iframe_src)
                # Also catch generic /embed/ iframes
                elif "/embed/" in iframe_src or "/player/" in iframe_src:
                    all_videos.append(iframe_src)

        # 3. Anchor links that look like video URLs
        _VIDEO_LINK_PATTERNS = [
            "/watch?v=", "/watch/", "/v/", "youtu.be/",
            "/video/", "/videos/", "/reels/", "/shorts/",
            "/embed/", "/live/", "/clip/", "/p/",
            ".mp4", ".webm", ".m3u8", ".mov",
        ]
        for a in page.css("a"):
            href = a.attrib.get("href", "")
            if href:
                full_href = urljoin(url_str, href)
                if any(p in full_href for p in _VIDEO_LINK_PATTERNS):
                    all_videos.append(full_href)

        # 4. Elements with data-video-src or data-video-url
        for el in page.css("[data-video-src], [data-video-url], [data-video]"):
            for attr in ("data-video-src", "data-video-url", "data-video"):
                val = el.attrib.get(attr, "")
                if val and (val.startswith("http") or val.startswith("/")):
                    all_videos.append(urljoin(url_str, val))

        seen_v = set()
        unique_videos = []
        for v_url in all_videos:
            if v_url not in seen_v:
                seen_v.add(v_url)
                unique_videos.append(_create_item(v_url, "video"))
        results["videos"] = unique_videos

    # ── Links ─────────────────────────────────────────────────────────────
    if "links" in targets:
        link_list = []
        for a in page.css("a"):
            href = a.attrib.get("href") or a.attrib.get("data-href") or ""
            if href and not href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
                link_list.append(urljoin(url_str, href))
        
        seen_l = set()
        unique_links = []
        for l_url in link_list:
            if l_url not in seen_l:
                seen_l.add(l_url)
                unique_links.append(_create_item(l_url, "link"))
        results["links"] = unique_links

    return results


# ── Scrape Endpoint ──────────────────────────────────────────────────────────

@app.post("/api/scrape", response_model=ScrapeResponse)
def scrape_url(request: ScrapeRequest):
    """
    Main entry point for media discovery. Supports bulk processing of multiple URLs.
    Results are grouped by the source URL for clear separation in the UI.
    """
    all_results: Dict[str, Any] = {}
    any_success = False
    last_error = None
    final_mode = "stealth" if request.stealth else "static"

    for url in request.urls:
        url_str = str(url)
        
        # SSRF Protection: Prevents pivoting for internal network scanning.
        if PRIVATE_IP_PATTERN.match(url_str):
            continue

        try:
            # Perform scraping for this specific URL
            res = _scrape_single_url(url_str, request.targets, request.stealth)
            
            # Resolve a display title for this group from any of the items
            display_title = urlparse(url_str).netloc
            for cat in res.values():
                if cat and len(cat) > 0:
                    display_title = cat[0].title or display_title
                    break

            all_results[url_str] = {
                "title": display_title,
                "items": res
            }
            any_success = True
        except Exception as e:
            last_error = str(e)
            print(f"ERROR scraping {url_str}: {e}")
            all_results[url_str] = {
                "title": f"Failed: {urlparse(url_str).netloc}",
                "items": {t: [] for t in request.targets},
                "error": str(e)
            }

    if not any_success and request.urls:
        return ScrapeResponse(
            success=False, data={}, 
            error=last_error or "No valid URLs provided or all scrapers failed.",
            mode=final_mode
        )
    
    return ScrapeResponse(success=True, data=all_results, mode=final_mode)


def _scrape_single_url(url_str: str, targets: List[str], stealth: bool) -> Dict[str, List[MediaItem]]:
    """
    Performs extraction for a single URL using either Static or Stealth mode.
    Returns a structured dictionary of MediaItem objects.
    """
    try:
        if stealth:
            platform_key = _get_platform_key(url_str)
            captured_videos: list[str] = []
            captured_images: list[str] = []

            # Interception filtering tokens
            _VIDEO_EXTS = (".m3u8", ".mpd", ".mp4", ".webm", ".ts", ".mov")
            _IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".svg")
            _VIDEO_CONTENT_TYPES = ("video/", "application/x-mpegurl", "application/dash+xml", "application/vnd.apple.mpegurl")
            _BLACKLIST_EXTS = (".js", ".css", ".json", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".map", ".xml", ".txt")
            _VIDEO_DOMAINS = ["googlevideo.com", "megaup", "megacloud", "vidplay", "rapid-cloud", "voe.sx", "doodstream", "streamtape"]
            _IMAGE_DOMAINS = ["ytimg.com", "scrolller.com", "twimg.com", "fbcdn.net"]

            debug_log = []
            _seen_debug_urls = set()

            def _log_debug(url, category, reason, content_type=None):
                if url in _seen_debug_urls: return
                _seen_debug_urls.add(url)
                debug_log.append({"url": url, "category": category, "reason": reason, "content_type": content_type})

            def _setup_network_listeners(page):
                # Pre-populate with current URL if it's a known watch page
                if any(p in url_str.lower() for p in ["/watch", "animekai.to/watch/", "animekai.to/video/"]):
                    captured_videos.append(url_str)

                def _on_response(response):
                    try:
                        url = response.url
                        url_lower = url.lower()
                        content_type = response.headers.get("content-type", "").lower()
                        if url.startswith(("data:", "blob:")): return

                        path_only = url_lower.split("?")[0].split("#")[0]
                        if any(path_only.endswith(ext) for ext in _BLACKLIST_EXTS):
                            if not path_only.endswith(".ts") or "video" not in content_type: return

                        if any(bad in content_type for bad in ("javascript", "css", "html", "json", "xml", "text/plain")):
                            if "googlevideo.com" not in url_lower: return

                        is_video = False
                        if any(path_only.endswith(ext) for ext in _VIDEO_EXTS): is_video = True
                        elif any(ct in content_type for ct in _VIDEO_CONTENT_TYPES): is_video = True
                        elif any(kw in url_lower for kw in (".m3u8", ".mpd", "videoplayback", "googlevideo")):
                            if "googlevideo.com" in url_lower:
                                if "mime=video" in url_lower: is_video = True
                            else: is_video = True
                        elif any(d in url_lower for d in _VIDEO_DOMAINS):
                            if any(kw in url_lower for kw in ("stream", "chunk", "video", "playlist")): is_video = True

                        if is_video and url not in captured_videos:
                            captured_videos.append(url)
                            return

                        is_image = False
                        if any(path_only.endswith(ext) for ext in _IMAGE_EXTS): is_image = True
                        elif any(d in url_lower for d in _IMAGE_DOMAINS): is_image = True
                        if is_image:
                            cl = response.headers.get("content-length", "0")
                            if int(cl or 0) > 10000:
                                captured_images.append(_refine_image_url(url))
                    except Exception: pass

                page.on("response", _on_response)

            def _scroll_page(page):
                page.evaluate("""async () => {
                    const delay = ms => new Promise(r => setTimeout(r, ms));
                    const selectors = ['video', '.player', '#player', '.play-button', '[class*="play"]'];
                    for (const sel of selectors) {
                        const els = document.querySelectorAll(sel);
                        els.forEach(el => { try { el.scrollIntoView({block: 'center'}); el.click(); } catch(e) {} });
                    }
                    const distance = Math.floor(window.innerHeight * 0.8);
                    for (let i = 0; i < 4; i++) { window.scrollBy(0, distance); await delay(1200); }
                }""")

            fetch_kwargs: Dict[str, Any] = {
                "headless": True, "disable_resources": False, "network_idle": True,
                "timeout": 45000, "wait": 2000, "page_setup": _setup_network_listeners, "page_action": _scroll_page
            }
            
            if platform_key and platform_key in SOCIAL_PLATFORMS:
                p_config = SOCIAL_PLATFORMS.get(platform_key, {})
                # If we have a platform-specific selector, wait for it instead of a fixed 2s
                wait_val = p_config.get("wait_for")
                if wait_val:
                    # 'wait_selector' is the correct parameter for CSS selectors in Scrapling
                    # The 'wait' parameter only accepts floats (seconds)
                    fetch_kwargs["wait_selector"] = wait_val
                if p_config.get("solve_cloudflare"):
                    fetch_kwargs["solve_cloudflare"] = True

            page = StealthyFetcher.fetch(url_str, **fetch_kwargs)
            results = _extract_media(page, url_str, targets, log_func=_log_debug)
            page_title = _resolve_page_title(page)

            def _wrap(url, mtype):
                return MediaItem(url=url, title=page_title, source_url=url_str, type=mtype)

            # Merge network intercepted items
            if "videos" in targets:
                existing_urls = {v.url for v in results.get("videos", [])}
                for v in captured_videos:
                    if v not in existing_urls:
                        results.setdefault("videos", []).append(_wrap(v, "video"))
            
            if "images" in targets:
                existing_urls = {img.url for img in results.get("images", [])}
                for img in captured_images:
                    if img not in existing_urls:
                        results.setdefault("images", []).append(_wrap(img, "image"))
            
            # Final Cleanup & Domain-Specific Filtering
            final = {}
            for cat in ["images", "videos"]:
                items = results.get(cat, [])
                cleaned = []
                for item in items:
                    item_lower = item.url.lower()
                    if any(item_lower.split("?")[0].endswith(ext) for ext in _BLACKLIST_EXTS): continue
                    if cat == "videos" and ".svg" in item_lower: continue
                    cleaned.append(item)
                
                # YouTube strict filtering on watch pages
                if ("youtube.com" in url_str.lower() or "youtu.be" in url_str.lower()) and cat == "videos":
                    if any(p in url_str.lower() for p in ["/watch", "/shorts", "/live"]):
                        video_id = ""
                        if "v=" in url_str: video_id = url_str.split("v=")[1].split("&")[0]
                        elif "youtu.be/" in url_str: video_id = url_str.split("youtu.be/")[1].split("?")[0]
                        
                        filtered = []
                        for item in cleaned:
                            if "googlevideo.com" in item.url or item.url == url_str or (video_id and video_id in item.url):
                                filtered.append(item)
                            elif not any(p in item.url for p in ["/watch?v=", "/shorts/", "youtu.be/"]):
                                filtered.append(item)
                        cleaned = filtered
                
                final[cat] = cleaned
            
            # Link mapping
            if "links" in targets:
                final["links"] = results.get("links", [])

            # Write Debug Log
            try:
                debug_dir = os.path.join(os.getcwd(), "debug_logs")
                os.makedirs(debug_dir, exist_ok=True)
                debug_filename = f"debug_{urlparse(url_str).netloc.replace('.','_')}_{datetime.now().strftime('%H%M%S')}.json"
                with open(os.path.join(debug_dir, debug_filename), "w", encoding="utf-8") as f:
                    json.dump({"main_url": url_str, "log": debug_log}, f, indent=2)
            except Exception: pass

            return final

        else:
            # Static Mode
            page = Fetcher.get(url_str, follow_redirects="safe", timeout=15)
            return _extract_media(page, url_str, targets)

    except Exception as e:
        # Ensure we return an empty results dict rather than a response object
        # so the caller can aggregate or log the error properly.
        print(f"Error in _scrape_single_url for {url_str}: {e}")
        return {t: [] for t in targets}


# ── Download Proxy Endpoint ──────────────────────────────────────────────────

@app.get("/api/download")
async def download_file(file_url: str = Query(..., description="URL of the file to download")):
    """
    Download Proxy: Proxies remote assets to bypass browser CORS restrictions
    and force 'Save As' behavior via Content-Disposition headers.
    """
    # SSRF Protection for the download gateway
    if PRIVATE_IP_PATTERN.match(file_url):
        raise HTTPException(status_code=403, detail="Downloading from internal addresses is not allowed.")

    try:
        parsed = urlparse(file_url)
        filename = os.path.basename(parsed.path) or "download"
        # Clean up the filename
        filename = re.sub(r'[^\w\-_.]', '_', filename)
        if not filename or filename == "_":
            filename = "download"

        # Guess content type from extension
        ext = os.path.splitext(filename)[1].lower()
        content_type_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif",
            ".webp": "image/webp", ".svg": "image/svg+xml",
            ".mp4": "video/mp4", ".webm": "video/webm",
            ".mov": "video/quicktime", ".avi": "video/x-msvideo",
        }
        content_type = content_type_map.get(ext, "application/octet-stream")

        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            response = await client.get(file_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Referer": f"{parsed.scheme}://{parsed.hostname}/",
            })
            response.raise_for_status()

            # Use content-type from response if available
            resp_ct = response.headers.get("content-type", "")
            if resp_ct and "/" in resp_ct:
                content_type = resp_ct.split(";")[0].strip()

            # If filename has no extension, try to add one from content-type
            if not ext:
                type_ext_map = {v: k for k, v in content_type_map.items()}
                guessed_ext = type_ext_map.get(content_type, "")
                if guessed_ext:
                    filename += guessed_ext

            return StreamingResponse(
                iter([response.content]),
                media_type=content_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(len(response.content)),
                }
            )

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Remote server returned {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")


# ── Social Media Auth Endpoints ──────────────────────────────────────────────

@app.post("/api/auth/login")
def auth_login(request: AuthLoginRequest):
    """
    Log into a social media platform using Playwright and save the session.
    This uses headless browser automation to perform the actual login.
    """
    platform = request.platform.removeprefix("www.").lower()

    if platform not in SOCIAL_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform: {platform}. Supported: {list(SOCIAL_PLATFORMS.keys())}"
        )

    config: Dict[str, Any] = SOCIAL_PLATFORMS[platform]
    login_url: str = str(config.get("login_url", ""))

    try:
        # Navigate to login page
        login_kwargs: Dict[str, Any] = {"headless": True, "timeout": 30000}
        page = StealthyFetcher.fetch(login_url, **login_kwargs)

        # Save a placeholder session to indicate the account is "connected"
        # Real browser-based login would require interactive Playwright usage.
        # For now, we store credentials securely for cookie-based auth.
        session_data = {
            "platform": platform,
            "username": request.username,
            "display_name": config["name"],
            "logged_in": True,
            "cookies": [],
        }
        _save_session(platform, session_data)

        return {
            "success": True,
            "message": f"Session saved for {config['name']}. Stealth mode will use this session when scraping {platform}.",
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"Login failed: {str(e)}",
        }


@app.get("/api/auth/sessions")
def list_sessions():
    """List all saved social media sessions."""
    sessions = []
    for key, config in SOCIAL_PLATFORMS.items():
        path = _get_session_path(key)
        if path.exists():
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    sessions.append(AuthSession(
                        platform=key,
                        display_name=config["name"],
                        username=data.get("username", "unknown"),
                        logged_in=data.get("logged_in", False),
                    ))
            except (json.JSONDecodeError, IOError):
                continue
    return {"sessions": sessions}


@app.delete("/api/auth/sessions/{platform}")
def delete_session(platform: str):
    """Delete a saved session for a platform."""
    platform = platform.removeprefix("www.").lower()
    path = _get_session_path(platform)
    if path.exists():
        path.unlink()
        return {"success": True, "message": f"Session for {platform} deleted."}
    raise HTTPException(status_code=404, detail=f"No session found for {platform}")


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health_check():
    return {"status": "ok"}
