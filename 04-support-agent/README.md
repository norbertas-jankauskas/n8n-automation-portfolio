# Support Agent (RAG + Web Search + Escalation)

Multi-tool AI agent answering questions about the CoinGecko API. Priority order: (1) search a vector knowledge base (Qdrant + Voyage AI embeddings), (2) fall back to web search (Tavily) if the knowledge base has no answer, (3) escalate to a human if neither source is confident.

**kb-search-tool.json** — sub-workflow: query → embedding → Qdrant search → formatted results. Also used to populate the knowledge base (scraping docs, chunking, embedding, upsert to Qdrant).

**support-agent.json** — main AI agent (LangChain, Claude Sonnet 5) calling kb-search-tool as a tool plus a Tavily web search tool.

**Nodes used:** AI Agent (LangChain), Anthropic Chat Model, Tool Workflow, HTTP Request Tool, Code, Split In Batches.
