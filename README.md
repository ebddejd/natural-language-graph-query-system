# Natural-Language Graph Query System

Deployment repository. `source.tar.gz` contains the complete Python, C++, frontend and test sources. The Docker build extracts and compiles these sources for Linux. No API credentials are included.

Render: Docker Web Service, Free instance, health check `/api/health`.
Set `LLM_API_KEY` and `LLM_MODEL` in Render Environment, then deploy. Visitors use the owner-funded shared AI model; graphs remain isolated by session. Optional `LLM_REQUESTS_PER_MINUTE` defaults to 20.

Extract sources with `tar -xzf source.tar.gz`. Run `make test` to test or `python3 app.py` for localhost use.
