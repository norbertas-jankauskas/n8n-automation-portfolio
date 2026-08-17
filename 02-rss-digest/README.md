# RSS Digest

Aggregates articles from multiple RSS feeds, filters out already-sent items via a Google Sheets dedup log, uses Claude to select and summarize the most relevant articles, and sends a formatted daily email.

**Nodes used:** Schedule Trigger, RSS Feed Read, Merge, Filter, HTTP Request (Anthropic), Google Sheets, Send Email.
