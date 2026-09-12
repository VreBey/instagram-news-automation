# Instagram AI & Gaming News Automation

[Türkçe](README.md) · **English**

A single-account automation system that collects daily AI and gaming news,
summarises it with Gemini, renders branded post/story/Reels media, and
publishes to Instagram on a schedule.

> **Nothing publishes without you by default.** `APPROVAL_MODE=telegram` is
> the default: every piece of content stops as a draft and asks for approval
> over Telegram. Fully automatic publishing is opt-in (`APPROVAL_MODE=auto`).

To see what it produces, run `python main.py --test` after installing; sample
posts, stories and a Reels video are written to `output/`. No API key is
needed — they are rendered on your machine with your own logo and settings.

> **Note on the source language.** The code, comments and commit history are
> in Turkish, and the `docs/` folder carries extended design notes that are
> not repeated here (scoring-coefficient measurements, queue-brake rationale,
> news-retirement logic). This document covers everything needed to install,
> configure, run and deploy the system.

---

## Architecture

```
Collect → Process → Generate media → Schedule → Publish
 (RSS /     (Gemini or    (Pillow /      (relevance    (Instagram
  NewsAPI /  template      MoviePy)       score         Graph API)
  Currents)  fallback)                    threshold)
```

| Module | Responsibility |
|---|---|
| [src/news_collector.py](src/news_collector.py) | Collects from RSS, NewsAPI and Currents; detects the same story reported by different outlets via `src/dedup.py` |
| [src/content_processor.py](src/content_processor.py) | Scores articles (`relevance_score`), generates captions/story/Reels scripts with Gemini or a template fallback |
| [src/image_generator.py](src/image_generator.py) | Feed post images (deterministic rotation across 3 templates) |
| [src/story_generator.py](src/story_generator.py) | Instagram Story images |
| [src/video_generator.py](src/video_generator.py) | Reels videos via Pillow + Edge-TTS + MoviePy |
| [src/instagram_client.py](src/instagram_client.py) | Graph API client, Cloudinary media upload, insights reads |
| [src/token_manager.py](src/token_manager.py) | Automatic refresh of the long-lived Instagram token |
| [src/scheduler.py](src/scheduler.py) | Orchestrates the whole pipeline and the daily schedule |
| [src/dashboard.py](src/dashboard.py) + [templates/index.html](templates/index.html) | Flask web panel — review queue, stats, insights |
| [src/database.py](src/database.py) | SQLite (WAL) data layer |

## How the pipeline works

Each `--pipeline` run (or the scheduled `--run` loop):

1. Articles are collected and de-duplicated.
2. Each article gets a `relevance_score` (0.0–1.0). Anything below
   `MIN_PROCESSING_SCORE` skips AI generation entirely to save Gemini quota.
   Generation is additionally capped by a **daily budget** and a **queue
   brake** (see [docs/tasarim-kararlari.md](docs/tasarim-kararlari.md) for the
   measurements behind both).
3. Media is rendered for the highest-scoring drafts.
4. *(only when `APPROVAL_MODE=auto`)* Content scoring above
   `AUTO_PUBLISH_THRESHOLD` with media ready is scheduled and published
   within the daily limits.
5. In the default mode content stays `draft` and a Telegram approval request
   is sent. The **Content** page in the dashboard shows the same queue.

---

## Requirements

- Python 3.10 or newer (production runs 3.10.12)
- Git
- `ffmpeg` — only if you use Reels generation

## Installation

```bash
git clone https://github.com/VreBey/instagram-news-automation.git
cd instagram-news-automation
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # Windows: copy .env.example .env
```

Then fill in `.env`. Which keys you actually need:

| Key group | Required? | Note |
|---|---|---|
| `CLOUDINARY_*` | **Yes** | The Graph API needs a public URL for media. Without these three **no publish can complete**, not even a manually triggered one. |
| `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` | Yes, to publish | See [docs/instagram-kurulumu.md](docs/instagram-kurulumu.md) |
| `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` | Recommended | Without them token auto-refresh does not work and the token must be renewed manually roughly every 60 days |
| `GEMINI_API_KEY` | Optional | Falls back to template-based captions without AI |
| `NEWS_API_KEY`, `CURRENTS_API_KEY` | Optional | Without them only RSS feeds are used, which still provides enough volume |
| `NVIDIA_API_KEY` | Optional | Embeddings only — paraphrase de-duplication and image/text relevance. Missing it **does not crash anything**, those two checks simply switch off |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Needed for the default mode | `APPROVAL_MODE=telegram` sends approval requests here |

### If you inherited this repository

No brand name, domain, server address or token is hardcoded — all of it comes
from configuration. To make it yours, fill in only these:

| Setting | Where | What happens if left empty |
|---|---|---|
| `BRAND_NAME` | `.env` | The panel title falls back to `Instagram News Automation` |
| `INSTAGRAM_USERNAME` | `.env` | The dashboard preview shows a `hesabiniz` placeholder |
| `BRAND_HANDLE` | `.env` | **No handle is stamped** on generated media (better blank than someone else's) |
| `CATEGORIES` | `config.py` | Category label, emoji and fallback hashtags — **the first thing to change for your own niche** |
| `REELS_CTA_TEXT`, `STORY_BADGE_TEXT` | `.env` | Reels outro call-to-action and story badge keep their Turkish defaults |
| `BOT_NAME`, `BOT_CONTACT_URL` | `.env` | RSS sources receive this repository's address as the bot identity |
| `BARE_REPO` | env var for `deploy/setup_vps.sh` | Defaults to `/opt/instagram-news.git` |
| `TUNNEL_NAME` | `/etc/default/instagram-tunnel` | Defaults to a tunnel named `instagram-news` |

Set `BOT_CONTACT_URL` to **your own** address. Crawling with someone else's
domain in the User-Agent is both misleading and puts their reputation at risk.

**Logos are not in the repository** — they are personal brand assets. Put your
own files in `assets/logos/`: `logo_horizontal.png` (top/bottom bar) and
`logo_mark.png` (Reels/story badge). Without them images are rendered without
a logo; the code handles the absence silently and nothing breaks.

---

## Commands

```bash
python main.py --collect      # Collect news
python main.py --process      # Process content (AI summary + scoring)
python main.py --generate     # Render media (images/video)
python main.py --publish      # Publish scheduled (auto + manually approved) content
python main.py --pipeline     # collect + process + generate + auto-schedule, one pass
python main.py --run          # Run the scheduler continuously (full daily cycle)
python main.py --dashboard    # Start the web panel (http://127.0.0.1:5000)
python main.py --stats        # Print statistics
python main.py --test         # Render test media (touches no API)
python main.py --maintenance  # Run maintenance now (backup + cleanup + VACUUM + disk)
python main.py --roundup      # Build the daily round-up carousel post
```

`--maintenance` is normally unnecessary — the scheduler runs it nightly at
02:00. It is useful right after a deploy so the first backup exists; otherwise
the health check warns "the database has never been backed up" until 02:00.

## Dashboard

`python main.py --dashboard`, then `http://127.0.0.1:5000`:

- **Dashboard** — overall stats, daily limits, token expiry warning
- **News** — every collected article with its score
- **Content** — drafts; items above `AUTO_PUBLISH_THRESHOLD` are marked and
  already scheduled, the rest can be approved manually
- **History** — everything published
- **Insights** — performance of published content (impressions, reach, likes,
  engagement rate), synced daily at 04:00

The dashboard binds to `127.0.0.1` only. `DASHBOARD_HOST` is deliberately
fixed — exposure to the internet is meant to go through Cloudflare Tunnel, not
by binding to a public interface.

## Configuration reference

All settings live in [config.py](config.py); most can be overridden from
`.env` (see [.env.example](.env.example)). The ones that matter most:

| Key | Default | Description |
|---|---|---|
| `AUTO_PUBLISH_THRESHOLD` | 0.75 | Scores above this publish automatically (auto mode only) |
| `MIN_PROCESSING_SCORE` | 0.35 | Below this, AI generation is skipped entirely |
| `MEDIA_GENERATION_MULTIPLIER` | 3 | How many times the daily limit to work on. **Also sets the daily news-processing budget** — raising it raises Gemini spend |
| `DEDUP_TITLE_SIMILARITY_THRESHOLD` | 0.82 | Above this title similarity, two articles are the same story |
| `DEDUP_WINDOW_HOURS` | 72 | Time window the de-duplication check looks back over |
| `DAILY_POST_LIMIT` / `_STORY_LIMIT` / `_REELS_LIMIT` | 2 / 5 / 1 | Daily publish quotas (**they bind the automatic path only**) |
| `TOKEN_REFRESH_WARNING_DAYS` | 10 | Auto-refresh is attempted this many days before expiry |
| `APPROVAL_MODE` | `telegram` | `telegram` = nothing publishes without approval; `auto` = threshold-based publishing |

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Tests make no real Instagram/Cloudinary/Gemini/RSS network calls, and each
test uses its own isolated temporary SQLite file (`tests/conftest.py`).
`tests/test_image_generator.py` does write real files, but into `output/posts/`
which is already gitignored.

---

## Deploying to a VPS

The system is designed to run 24/7 on a small Ubuntu 22.04/24.04 VPS.
Everything needed is in [deploy/](deploy/):

- `deploy/systemd/*.service` — five units: scheduler (`--run`), MCP server,
  Telegram bot, dashboard, and a single Cloudflare Named Tunnel service that
  exposes both the MCP server and the dashboard.
- `deploy/setup_vps.sh` — run as `root` after the project files are in place.
  Installs system packages and `cloudflared`, creates an `appuser`, builds the
  virtualenv, installs and starts the four application services. If
  `DASHBOARD_PASSWORD` is empty it warns and waits 10 seconds, to make it
  harder to accidentally expose an unauthenticated panel.
- `deploy/post-receive` — a git hook enabling `git push <remote> master`
  deploys. It checks the pushed `master` into the working tree, runs
  `git clean -fd` while excluding `.env`, `data`, `logs`, `assets`, `output`
  and `venv`, then restarts the services.

Rough order: create the VPS → add your SSH key → copy the project to
`/opt/instagram-otomasyon` → run `bash deploy/setup_vps.sh` → set up the
Cloudflare tunnel (a one-time step that needs a browser login, so it cannot
be automated; [docs/dagitim.md](docs/dagitim.md) documents it step by step).

Rollback after a bad deploy: `git push <remote> <older-sha>:master --force`.

## Backup and recovery

A nightly backup of the database is taken automatically. **It is written to
the same disk**, so it does not survive losing the server — an off-site copy
is on you.

The single most important file to keep off the server is `.env`. You can
restore the database and still have a dead system without it; that file is
what actually determines your recovery time.

---

## Documentation

The `docs/` folder is written in Turkish, but the file names and code
references are self-explanatory and machine translation handles them well.

| Document | Contents |
|---|---|
| [docs/instagram-kurulumu.md](docs/instagram-kurulumu.md) | Obtaining and refreshing the Instagram token |
| [docs/icerik-uretimi.md](docs/icerik-uretimi.md) | Daily round-up, Reels, visual design, Telegram bot |
| [docs/dagitim.md](docs/dagitim.md) | VPS setup, Cloudflare tunnel, backup restore |
| [docs/mcp-koprusu.md](docs/mcp-koprusu.md) | External AI agent bridge (optional) |
| [docs/tasarim-kararlari.md](docs/tasarim-kararlari.md) | Why it works this way — measurements and rationale |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, conventions, what is out of scope |
| [SECURITY.md](SECURITY.md) | Where secrets live, and the limits of log redaction |

Read **tasarim-kararlari** before changing behaviour. Most numbers there are
measured, not guessed, and a few were learned the hard way.

## Known limitations

- **Single account, single machine.** Multi-account or containerised
  deployment was never a goal; this is a deliberate scope decision.
- **One SQLite file, four processes.** The scheduler, dashboard, Telegram bot
  and MCP server share `news.db`. WAL and a 30s `busy_timeout` are enabled,
  but `database is locked` can still appear under load (it cost 11 publishes
  on 31 July 2026). Funnelling writes through one process is the correct fix
  and has not been done yet.
- **Third-party press images are used.** The background chain prefers the
  article's own image and its `og:image`. Attribution is given but no licence
  is obtained — an accepted copyright risk.
- **Local development steals production's Telegram bot.** Two `getUpdates`
  loops cannot share a token (409 Conflict). Use a separate bot token locally.
- **Performance data depends on the `instagram_manage_insights` permission.**
  Without it Meta returns `(#10) Application does not have permission` even
  when the metric names are right, and the insights table stays empty. This is
  a Meta app-settings issue, not something fixable in code.
- **Story insights last 24 hours.** After a story expires the media object no
  longer resolves, so stories older than 24 hours are excluded from insights.

### Current status: Instagram publishing is blocked

On 23 August 2026 Meta placed an API lock on the account (error code 200).
Posts and stories cannot be published through the API. The system still
collects, scores and generates content and prepares a manual-publish package.
**This is not an installation problem** — a fresh install behaves the same
way. Resolving it requires action on Meta's side.

---

## Security

Secrets live only in `.env`, which is gitignored. Log redaction is installed
by the entry points, not automatically — if you run the app by importing it
directly, install the filter yourself. See [SECURITY.md](SECURITY.md).

## Contributors

- [VreBey](https://github.com/VreBey) — product, design decisions, operations
- Claude (Anthropic) — code, tests and documentation via
  [Claude Code](https://claude.com/claude-code)

## License

[MIT](LICENSE) — covers the source code only.

Fonts, images fetched at runtime and trademarks are out of scope; see
[NOTICE.md](NOTICE.md).
