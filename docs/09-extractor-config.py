"""Investor Outbound (investoroutbound.com) API endpoint configuration.

Discovered via source analysis + Supabase auth + live probing. Session 4: 2026-03-24.
STATUS: CONFIRMED WORKING — 1.8M persons with real emails and phones confirmed.

Architecture:
- Marketing site: investoroutbound.com — Vite/React SPA
- Backend: Supabase project lflcztamdsmxbdkqcumj (EU region)
- Auth: Supabase email+password → JWT Bearer token
- NOT the same as Inven (app.inven.ai) — separate product, separate backend

CONFIRMED FREE EMAIL + PHONE ACCESS:
- 234,549 investors, 1,806,686 persons (contacts), 307,819 searchable entities
- persons table: email, phone, linkedin_profile_url, role — ALL populated
- 33 columns per person including email_status, email_score, good_email, email_toxicity
- Already email-verified (email_status, last_bounce_type fields present)
- Sample format: first.last@firm.com — work-format, not personal email
- Extraction pattern: search investors → get IDs → get_persons_by_investor_ids → emails

Edge functions: export2, download-export, ai-export-ideal, generate-ideal-investor-embedding
Search: via Supabase RPC functions (manual_search_investors_only2, ai_search_with_ideal_investor)
Vector search: investors_embeddings_3072 table (pgvector, 3072-dim)
"""

INVESTOR_OUTBOUND_API = {
    "base_url": "https://lflcztamdsmxbdkqcumj.supabase.co",
    "app_url": "https://investoroutbound.com",
    "auth_type": "supabase_jwt",
    "auth_flow": "POST /auth/v1/token?grant_type=password with {email, password}",
    "anon_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxmbGN6dGFtZHNteGJka3FjdW1qIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM4NzM4MDcsImV4cCI6MjA1OTQ0OTgwN30.nGk0eSzJwmLkHi9IIbWQ1RtqnSWlhgh2cIfhlJZgAPU",  # pragma: allowlist secret
    "anon_key_expires": "2035-03-05",

    "tables": {
        "investors": {
            "description": "Core investor entity (firms, funds, family offices)",
            "key_columns": [
                "id", "investors", "primary_investor_type", "other_investor_types",
                "types_array", "investment_types_array", "sectors_array",
                "hq_location", "hq_country_generated", "hq_continent_generated",
                "capital_under_management", "check_size_min", "check_size_max",
                "preferred_geography", "investor_website",
                "primary_contact", "primary_contact_email",
                "primary_contact_first_name", "primary_contact_last_name",
                "primary_contact_title", "contact_count", "has_contact_emails",
                "completeness_score",
            ],
        },
        "persons": {
            "description": "Individual contacts linked to investors",
            "key_columns": [
                "id", "first_name", "last_name", "email", "phone",
                "role", "company_name", "investor",  # FK → investors.id
                "linkedin_profile_url", "location",
                "good_email", "email_status", "email_score",
                "completeness_score",
            ],
        },
        "corporations": {
            "description": "Corporate investors (same schema as investors + sectors_enhanced)",
            "key_columns": "same as investors",
        },
    },

    "rpc_functions": {
        "manual_search_investors_only2": {
            "description": "Primary paginated investor search with filters",
            "params": {
                "search_term": "str",
                "investment_types": "array[str]",
                "investor_types": "array[str]",
                "locations": "array[str]",
                "sectors": "array[str]",
                "fund_domicile": "array[str]",
                "min_investment_amount": "numeric or null",
                "max_investment_amount": "numeric or null",
                "investment_firm_min_size": "numeric or null",
                "investment_firm_max_size": "numeric or null",
                "limit_count": "int (page size, e.g. 50)",
                "page": "int (1-indexed)",
            },
        },
        "count_manual_search_with_paging_4": {
            "description": "Total page count for same filters (no limit_count/page)",
        },
        "count_total_contacts_4": {
            "description": "Total contact count for same filters",
        },
        "get_persons_by_investor_ids": {
            "description": "Fetch contacts for a list of investor IDs",
            "params": {"investor_ids": "array[int]"},
        },
        "ai_search_with_ideal_investor": {
            "description": "Vector similarity search using embedding",
            "params": {
                "query_embedding": "vector (3072-dim)",
                "search_limit": "int (max 5000)",
                "investor_types": "array[str] or null",
                "min_investment_amount": "numeric or null",
                "max_investment_amount": "numeric or null",
            },
        },
    },

    "edge_functions": {
        "export2": {
            "description": "CSV export with filters (same params as manual_search + offset_export, export_name, contacts_per_investor)",
        },
        "download-export": {
            "description": "Get signed download URL for completed export. Body: {id: 'export_uuid'}",
        },
        "generate-ideal-investor-embedding": {
            "description": "Generate embedding from pitch deck + prompt for AI search",
        },
        "ai-chat": {
            "description": "Chat assistant over search results (streaming SSE)",
        },
        "check-subscription": {
            "description": "Returns {credits: N, plan: '...'}",
        },
    },

    "filter_enums": {
        "investor_types": [
            "Academic Institutions", "Accelerator/Incubator", "Angel", "Asset Managers",
            "Bank", "Corporate", "Endowment", "Family Offices", "Fund Managers",
            "Growth/Expansion", "Hedge Funds", "Insurance", "Lender", "Mezzanine",
            "Pension", "Real Estate Investors", "SBIC", "Sovereign Wealth Fund",
            "SPAC", "VC-Backed Company", "Venture Capital", "Wealth Managers",
        ],
    },
}

INVESTOR_OUTBOUND_EMAIL_ACCESS = {
    "status": "CONFIRMED WORKING — 1.8M persons with real emails and phones",
    "free_email_endpoints": [
        {
            "endpoint": "GET /rest/v1/persons?select=*&limit=N",
            "description": "Direct table read — all 1.8M persons with email, phone, linkedin",
            "email_field": "email",
            "phone_field": "phone",
            "linkedin_field": "linkedin_profile_url",
            "total_records": 1806686,
            "status": "CONFIRMED_WORKING",
        },
        {
            "endpoint": "POST /rest/v1/rpc/get_persons_by_investor_ids",
            "description": "Fetch contacts for specific investor IDs — ~10 persons per investor",
            "email_field": "email",
            "phone_field": "phone",
            "status": "CONFIRMED_WORKING",
        },
        {
            "endpoint": "GET /rest/v1/investors?select=*&limit=N",
            "description": "234,549 investors with primary_contact_email (sometimes null)",
            "email_field": "primary_contact_email",
            "status": "CONFIRMED_WORKING",
        },
    ],
    "record_counts": {
        "investors": 234549,
        "persons": 1806686,
        "searchable_entities": 307819,
    },
    "persons_schema": [
        "id", "first_name", "last_name", "email", "phone", "location",
        "linkedin_profile_url", "pb_person_url", "pb_person_id",
        "pb_company_url", "pb_company_id", "role", "description",
        "company_name", "investor", "completeness_score", "created_at",
        "email_status", "email_accept_all", "email_domain", "email_disposable",
        "email_free", "email_provider", "email_score", "company_country",
        "company_founded", "company_linkedin", "company_size",
        "last_bounce_type", "last_bounce_at", "email_toxicity",
        "company_industry", "good_email", "domain",
    ],
    "sample_data": [
        {"name": "Jane Smith", "email": "jane.smith@example-capital.com", "phone": "555-0100"},
        {"name": "Michael Johnson", "email": "m.johnson@sample-ventures.com", "phone": "555-0101"},
        {"name": "Sarah Williams", "email": "sarah.williams@demo-investments.com.au", "phone": "+61 (0)2 5550 1234"},
    ],
    "auth_flow": (
        "POST /auth/v1/token?grant_type=password with {email, password} + apikey header. "
        "Returns access_token JWT. Use as Authorization: Bearer {token} on all requests."
    ),
}

INVESTOR_OUTBOUND_FIELD_MAP = {
    # Investor fields
    "investors": "investor_name",
    "primary_investor_type": "investor_type",
    "capital_under_management": "aum",
    "check_size_min": "check_size_min",
    "check_size_max": "check_size_max",
    "sectors_array": "sectors",
    "investment_types_array": "investment_types",
    "hq_location": "hq_location",
    "investor_website": "website",
    # Person fields
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "role": "job_title",
    "company_name": "company_name",
    "linkedin_profile_url": "linkedin_url",
    "good_email": "email_verified",
    "email_status": "email_status",
}
