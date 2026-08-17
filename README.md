# n8n Automation Portfolio

Self-taught n8n automation projects. Code was written by AI coding agents (Claude, ChatGPT) under my direction — I own the logic, testing, and debugging.

## Projects

1. **[Bitcoin Price Watcher](./01-bitcoin-price-watcher)** — crypto price monitoring, conditional logic, Telegram alerts, results logged to Google Sheets.
2. **[RSS Digest](./02-rss-digest)** — aggregates multiple RSS sources, filters duplicates, uses Claude to select and summarize the most relevant articles, sends a formatted email.
3. **[Gmail Tracker](./03-gmail-tracker)** — classifies incoming Gmail messages with AI (receipt / job application / unrecognized), extracts structured fields, logs to separate Google Sheets tabs with duplicate protection.
4. **[Support Agent](./04-support-agent)** — multi-tool AI agent with a vector knowledge base (Qdrant + Voyage AI embeddings), web search fallback (Tavily), and escalation to a human when confidence is low.

## Security note

All credentials are exported as internal n8n references only (n8n never exports real API keys). Personal identifiers (Google Sheets document IDs, webhook IDs, n8n instance ID) have been replaced with placeholders before publishing.
