# Demo: screen capture guide

This folder holds **demo screenshots** for presentations, docs, and the blog (`APPLICATION_BLOG.md`).  
Add PNG files under `screenshots/` using the filenames below so embedded images resolve.

## Prerequisites before recording

- `DEEPSEEK_API_KEY` set; for KCS demos, `REDHAT_OFFLINE_TOKEN` set.
- Three processes running (see `cmds` in the repo root):

  ```text
  ./web-search-agent-deepseek.py --port 8080
  ./redhat-kcs-agent-deepseek.py --port 8081
  ./a2a_web_server.py --concierge-port 9996 -v
  ```

## Shot list (suggested order)

| File | What to capture |
|------|------------------|
| `01-three-terminals.png` | **Overview:** three terminal panes or windows showing the web-search agent, Red Hat KCS agent, and concierge server running (blur or crop secrets). |
| `02-concierge-handoffs.png` | Concierge **startup log** lines showing both sub-agents discovered, e.g. `WebSearchAgent-DeepSeek` and `RedHatKCSAgent-DeepSeek` on their ports. |
| `03-web-ui-general.png` | **Flask UI** (`web_UI.py`) after asking a clearly *non–Red Hat* question; reply should cite web search / URLs. |
| `04-web-ui-redhat.png` | Same UI with a **Red Hat–centric** question (e.g. RHEL subscription or OpenShift); reply should lean on KCS / access.redhat.com links. |
| `05-cli-client.png` | **`a2a_web_client.py`** (or similar) in a terminal showing a prompt and assistant response. |
| `06-optional-kcs-agent-logs.png` | Optional: **redhat-kcs-agent** terminal showing an "Enhanced KCS query" print line for a Red Hat query (redact tokens). |

## Tips

- **Resolution:** 1280×720 or 1920×1080 is enough for slides; crop to the relevant pane.
- **Privacy:** blur API keys, tokens, hostnames if needed.
- **Naming:** keep the filenames above so `APPLICATION_BLOG.md` links stay valid.

## Empty gallery

Until you add files, the blog’s Demo section will show missing images in some Markdown viewers. Replace each `screenshots/*.png` with your captures when ready.
