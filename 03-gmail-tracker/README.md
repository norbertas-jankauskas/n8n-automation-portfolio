# Gmail Tracker

Classifies incoming Gmail messages with AI (receipt / job application / unrecognized) via Claude tool-use, extracts structured fields (vendor, amount, company, status), and logs results to separate Google Sheets tabs with duplicate protection by message ID.

**Nodes used:** Gmail Trigger, HTTP Request (Anthropic, tool use), Switch, Google Sheets (lookup + append), IF (dedup check).
