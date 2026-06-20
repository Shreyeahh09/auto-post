# OpenInstaFlow (Python) — Instagram MCP server

A Python port of [OpenInstaFlow](https://github.com/OpenInstaFlow/openinstaflow) — an [MCP](https://modelcontextprotocol.io) server that exposes the **Instagram Graph API** to any MCP client (Claude Desktop, Claude Code, Cursor, …). **Bring your own Meta token** — point the server at your Instagram Business/Creator account and the model can read your profile, posts and insights, publish media, manage Facebook Pages, and handle DMs.

## Features

**🔧 Tools (model-controlled)**

| Tool | What it does |
|---|---|
| `get_profile_info` | Profile details: username, bio, website, follower/following/media counts, avatar |
| `get_media_posts` | Recent posts (caption, type, permalink, like/comment counts) with paging |
| `get_media_insights` | Per-post engagement (reach, likes, comments, saves, shares, …) |
| `publish_media` | Publish an **image / reel / story / carousel** from public media URLs |
| `get_account_pages` | List connected Facebook Pages + their Page tokens + linked IG account *(FB Login)* |
| `get_conversations` | List Instagram DM threads *(Advanced Access)* |
| `get_conversation_messages` | Read messages in a thread *(Advanced Access)* |
| `send_dm` | Reply to a DM *(Advanced Access)* |

**📊 Resources (application-controlled)** — live, attachable context:
`instagram://profile`, `instagram://media`, `instagram://insights`.

**💬 Prompts (user-controlled)** — ready-made templates:
`analyze_engagement`, `content_strategy`, `hashtag_analysis`.

## Quick start

### Installation

```bash
pip install -e .
```

### MCP client config

Add it to your MCP client config (Claude Desktop: `claude_desktop_config.json`; Claude Code: `.mcp.json`):

```json
{
  "mcpServers": {
    "openinstaflow": {
      "command": "python",
      "args": ["-m", "openinstaflow"],
      "env": {
        "IG_ACCESS_TOKEN": "<your long-lived token>",
        "IG_USER_ID": "<your IG business account id>",
        "IG_LOGIN_KIND": "ig_login"
      }
    }
  }
}
```

Or run directly:

```bash
python -m openinstaflow
```

## Authentication

Set these in the server's `env` block (above):

| Var | Required | Notes |
|---|---|---|
| `IG_ACCESS_TOKEN` | ✅ | Long-lived Instagram/Meta access token with content-publishing scope |
| `IG_USER_ID` | ✅ (most tools) | Your Instagram **Business/Creator** account id |
| `IG_LOGIN_KIND` | – | `ig_login` (default, `graph.instagram.com`) or `fb_login` (`graph.facebook.com`, **required for Pages + DMs**) |
| `IG_GRAPH_VERSION` | – | Graph version for the facebook.com base (default `v23.0`) |
| `FB_PAGE_ID` / `FB_PAGE_ACCESS_TOKEN` | – | Page id + token for Pages/DM tools (FB Login) |
| `META_APP_ID` / `META_APP_SECRET` | – | Only needed if you add token-refresh/debug flows |

Every tool also accepts optional `access_token` / `ig_user_id` arguments that **override** the env for that single call — so one running server can drive multiple accounts.

### Permissions

- **Reading + publishing** need a Business/Creator account and the usual content-publishing permissions (`instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`).
- **DM tools** (`get_conversations`, `get_conversation_messages`, `send_dm`) need **Advanced Access** with `instagram_manage_messages`, and (for FB Login) a Page token.

### Media must be a public URL

Instagram fetches media **server-side**, so `publish_media` takes **public HTTPS URLs** (`image_url` / `video_url` / carousel item urls) — not local files.

## Development

```bash
pip install -e .                    # install in dev mode
python -m openinstaflow             # run the MCP server on stdio
```

## License

MIT
