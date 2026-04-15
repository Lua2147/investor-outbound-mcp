"""Sector, investor type, and investment type resolution for Investor Outbound.

The IO database uses:
- snake_case sector codes in sectors_array (e.g., 'energy', 'cleantech')
- Proper-case investor type strings in primary_investor_type (e.g., 'Venture Capital')
- Proper-case investment type strings in investment_types_array (e.g., 'Seed Round')

This module maps human-readable inputs to the actual DB values, sourced from a live
1K-row sample (docs/12-real-enums-from-1k-sample.json) and a 307K investor probe.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Sector codes (sectors_array)
# ---------------------------------------------------------------------------

# Human-readable → DB codes mapping (many-to-many)
SECTOR_MAP: dict[str, list[str]] = {
    # Energy & Infrastructure
    "energy": ["energy", "cleantech", "clean_tech", "renewableenergy", "green_energy",
               "newenergy", "right_energy", "rightenergy", "gayenergysolarcleantechwindrenewables"],
    "infrastructure": ["greeninfrastructure", "sustainableinfrastructure"],
    "utilities": ["utilities"],
    "oil & gas": ["oil_gas", "rightenergyoilgascoal"],

    # Healthcare
    "healthcare": ["healthcare", "health_care", "health", "healthtech",
                   "healthcaretechnology"],
    "biotech": ["biotech"],
    "pharma": ["pharma"],

    # Technology
    "technology": ["technology", "digitaltechnology", "informationtechnology",
                   "informationtechnologyservices"],
    "software": ["software", "software_saas", "enterprisesoftware"],
    "ai/ml": ["ai_ml", "artificialintelligence"],
    "cybersecurity": ["cybersecurity"],

    # Financial
    "fintech": ["fintech"],
    "financial services": ["fin_services", "finservices", "financialservices"],
    "financial investments": ["fin_invest", "fininvest", "financeinvestments"],
    "insurance": ["insurance", "insurancetech"],

    # Consumer
    "consumer": ["consumer", "consumer_discr", "consumerdiscr", "consumerdiscretionary",
                 "consumerproducts", "consumer_staples", "consumerstaples"],
    "retail": ["retail", "ecommerce", "e_commerce"],

    # Real Estate
    "real estate": ["realestate", "real_estate", "realestatedirectinvestments",
                    "realestategplp", "realestateindustrial/logistics",
                    "realestatelending", "realestateoffice", "realestateretail",
                    "multifamilyrealestate", "re", "re_lending", "relending"],

    # Industrial
    "industrial": ["industrial", "industrials", "industrialtechnology", "manufacturing"],

    # Automotive & Transport
    "automotive": ["automotive", "mobility", "transports"],

    # Crypto & Blockchain
    "blockchain": ["blockchain", "crypto"],

    # Other
    "education": ["education", "edtech"],
    "media": ["media", "media_tv", "mediatelevision", "mediatv"],
    "gaming": ["gaming_esports"],
    "agriculture": ["agriculture", "agriculturaltechnology", "agritech"],
    "space": ["space_tech"],
    "cannabis": ["cannabis"],
    "mining": ["mining", "miningdevelopment", "miningexploration"],
    "water": ["water"],
    "services": ["services", "business_services", "businessservices",
                 "business_products_and_services"],
    "private equity": ["private_equity", "privateequity", "venturecapital"],
    "impact": ["esg_impact", "impactinvesting"],
    "nanotechnology": ["nanotechnology"],
}

# Flatten: all known DB codes
ALL_SECTOR_CODES = sorted(set(code for codes in SECTOR_MAP.values() for code in codes))


# ---------------------------------------------------------------------------
# Investor types (primary_investor_type)
# All 48 values from docs/12-real-enums-from-1k-sample.json + docs/06-filter-enums.md
# ---------------------------------------------------------------------------

INVESTOR_TYPES: list[str] = [
    "Academic Institution",
    "Accelerator/Incubator",
    "Angel (individual)",
    "Angel Group",
    "Asset Manager",
    "Asset Manager - Fund Manager",
    "Bank",
    "Corporate Development",
    "Corporate Investor",
    "Corporate Venture Capital",
    "Endowment Plan",
    "Family Office",
    "Family Office - Multi",
    "Family Office - Single",
    "Financial Advisor",
    "Foundation",
    "Fund Manager",
    "Fund of Funds",
    "Fund of Hedge Funds Manager",
    "Fundless Sponsor",
    "Government",
    "Growth/Expansion",
    "Hedge Fund",
    "Holding Company",
    "Impact Investing",
    "Infrastructure",
    "Investment Bank",
    "Investment Company",
    "Lender/Debt Provider",
    "Limited Partner",
    "Merchant Banking Firm",
    "Mutual Fund",
    "Not-For-Profit Venture Capital",
    "Other",
    "Other Private Equity",
    "PE/Buyout",
    "Private Equity",
    "Private Equity Firm",
    "Private Equity Fund of Funds Manager",
    "Private Sector Pension Fund",
    "Public Pension Fund",
    "Real Estate",
    "Real Estate Film (Investor)",
    "Real Estate Firm",
    "Real Estate Fund of Funds Manager",
    "Secondary Buyer",
    "Sovereign Wealth Fund",
    "Special Purpose Acquisition Company (SPAC)",
    "University",
    "Venture Capital",
    "Wealth Management/RIA",
    "Wealth Manager",
]

# Human-readable → DB values mapping for primary_investor_type
_INVESTOR_TYPE_MAP: dict[str, list[str]] = {
    # VC / growth
    "vc": ["Venture Capital"],
    "venture capital": ["Venture Capital"],
    "venture": ["Venture Capital"],
    "cvc": ["Corporate Venture Capital"],
    "corporate vc": ["Corporate Venture Capital"],
    "corporate venture": ["Corporate Venture Capital"],
    "growth": ["Growth/Expansion"],
    "growth equity": ["Growth/Expansion"],
    "not-for-profit vc": ["Not-For-Profit Venture Capital"],
    "nfp vc": ["Not-For-Profit Venture Capital"],

    # PE / buyout
    "pe": ["PE/Buyout", "Private Equity", "Private Equity Firm"],
    "buyout": ["PE/Buyout"],
    "private equity": ["PE/Buyout", "Private Equity", "Private Equity Firm"],
    "pe/buyout": ["PE/Buyout"],
    "other pe": ["Other Private Equity"],
    "pe fund of funds": ["Private Equity Fund of Funds Manager"],

    # Family office
    "family office": ["Family Office", "Family Office - Single", "Family Office - Multi"],
    "single family office": ["Family Office - Single"],
    "multi family office": ["Family Office - Multi"],
    "sfo": ["Family Office - Single"],
    "mfo": ["Family Office - Multi"],

    # Wealth management / RIA
    "ria": ["Wealth Management/RIA"],
    "wealth management": ["Wealth Management/RIA", "Wealth Manager"],
    "wealth manager": ["Wealth Manager"],
    "financial advisor": ["Financial Advisor"],
    "advisor": ["Financial Advisor"],

    # Hedge fund
    "hedge fund": ["Hedge Fund"],
    "hf": ["Hedge Fund"],
    "fund of hedge funds": ["Fund of Hedge Funds Manager"],

    # Angel
    "angel": ["Angel (individual)", "Angel Group"],
    "angel individual": ["Angel (individual)"],
    "angel group": ["Angel Group"],

    # Asset manager
    "asset manager": ["Asset Manager", "Asset Manager - Fund Manager"],
    "fund manager": ["Fund Manager", "Asset Manager - Fund Manager"],

    # Real estate
    "real estate": ["Real Estate", "Real Estate Firm", "Real Estate Film (Investor)"],
    "reit": ["Real Estate"],
    "real estate fund of funds": ["Real Estate Fund of Funds Manager"],

    # Debt / lending
    "lender": ["Lender/Debt Provider"],
    "debt": ["Lender/Debt Provider"],
    "debt provider": ["Lender/Debt Provider"],
    "mezzanine": ["Lender/Debt Provider"],

    # Infrastructure
    "infrastructure": ["Infrastructure"],

    # Accelerator / incubator
    "accelerator": ["Accelerator/Incubator"],
    "incubator": ["Accelerator/Incubator"],
    "accelerator/incubator": ["Accelerator/Incubator"],

    # Impact
    "impact": ["Impact Investing"],
    "impact investing": ["Impact Investing"],
    "esg": ["Impact Investing"],

    # Corporate / strategic
    "corporate": ["Corporate Investor", "Corporate Development"],
    "corporate investor": ["Corporate Investor"],
    "corporate development": ["Corporate Development"],
    "strategic": ["Corporate Investor", "Corporate Development"],

    # Institutional
    "pension": ["Private Sector Pension Fund", "Public Pension Fund"],
    "pension fund": ["Private Sector Pension Fund", "Public Pension Fund"],
    "public pension": ["Public Pension Fund"],
    "private pension": ["Private Sector Pension Fund"],
    "endowment": ["Endowment Plan"],
    "sovereign wealth": ["Sovereign Wealth Fund"],
    "swf": ["Sovereign Wealth Fund"],
    "university": ["University"],
    "foundation": ["Foundation"],

    # Fund structures
    "fund of funds": ["Fund of Funds"],
    "fof": ["Fund of Funds"],
    "spac": ["Special Purpose Acquisition Company (SPAC)"],
    "secondary": ["Secondary Buyer"],
    "secondaries": ["Secondary Buyer"],
    "fundless sponsor": ["Fundless Sponsor"],
    "search fund": ["Fundless Sponsor"],

    # Banks / merchant banking
    "bank": ["Bank"],
    "investment bank": ["Investment Bank"],
    "merchant bank": ["Merchant Banking Firm"],

    # Holding companies
    "holding company": ["Holding Company"],
    "holdco": ["Holding Company"],
    "conglomerate": ["Holding Company"],

    # Mutual fund
    "mutual fund": ["Mutual Fund"],

    # Other
    "limited partner": ["Limited Partner"],
    "lp": ["Limited Partner"],
    "government": ["Government"],
    "other": ["Other"],
    "investment company": ["Investment Company"],
    "holding": ["Holding Company"],
}


# ---------------------------------------------------------------------------
# Investment types (investment_types_array)
# All 77 values from docs/12-real-enums-from-1k-sample.json
# ---------------------------------------------------------------------------

INVESTMENT_TYPES: list[str] = [
    "Accelerator/Incubator",
    "Acquisition Financing",
    "Add-on",
    "Angel (individual)",
    "Asset Acquisition",
    "Asset Divestiture (Corporate)",
    "Balanced",
    "Bankruptcy: Admin/Reorg",
    "Bankruptcy: Liquidation",
    "Bonds",
    "Bonds (Convertible)",
    "Bridge",
    "Buyout",
    "Buyout/LBO",
    "Capital Spending",
    "Capitalization",
    "Carveout",
    "CLO",
    "Co-investment",
    "Convertible Debt",
    "Corporate",
    "Corporate Asset Purchase",
    "Corporate Divestiture",
    "Debt - General",
    "Debt Refinancing",
    "Debt Repayment",
    "Distressed Acquisition",
    "Distressed Debt",
    "Dividend Recapitalization",
    "Early Stage",
    "Early Stage VC",
    "Equity For Service",
    "Expansion / Late Stage",
    "Fund of Funds",
    "General Corporate Purpose",
    "Grant",
    "Growth",
    "Hospital/Healthcare Facility",
    "Hotel",
    "IPO",
    "Joint Venture",
    "Late Stage Venture",
    "Later Stage VC",
    "Leveraged Recapitalization",
    "Loan",
    "Management Buy-In",
    "Management Buyout",
    "Merger/Acquisition",
    "Mezzanine",
    "Natural Resources",
    "PE Growth/Expansion",
    "PIPE",
    "Privatization",
    "Project Financing",
    "Public to Private",
    "Public-Private Partnership",
    "Real Estate",
    "Recapitalization",
    "Sale-Lease back facility",
    "Secondaries",
    "Secondary Buyer",
    "Secured",
    "Secured Debt",
    "Seed",
    "Seed Round",
    "Senior Debt",
    "Special Situations",
    "Spin-Off",
    "Start-up",
    "Subordinated",
    "Subordinated Debt",
    "Timber",
    "Turnaround",
    "University Spin-Out",
    "Unsecured Debt",
    "Venture (General)",
    "Venture Debt",
    "Working Capital",
]

# Human-readable → DB values mapping for investment_types_array
_INVESTMENT_TYPE_MAP: dict[str, list[str]] = {
    # Seed / early
    "seed": ["Seed Round", "Seed", "Angel (individual)", "Start-up"],
    "seed round": ["Seed Round"],
    "pre-seed": ["Seed", "Angel (individual)", "Start-up"],
    "angel": ["Angel (individual)", "Seed"],
    "start-up": ["Start-up"],
    "startup": ["Start-up"],

    # Series A / early VC
    "series a": ["Early Stage VC", "Early Stage", "Venture (General)"],
    "early stage": ["Early Stage", "Early Stage VC", "Venture (General)"],
    "early stage vc": ["Early Stage VC"],

    # Series B+ / later VC
    "series b": ["Later Stage VC", "Venture (General)", "Growth"],
    "series c": ["Later Stage VC", "Growth", "PE Growth/Expansion"],
    "series d": ["Later Stage VC", "Growth", "PE Growth/Expansion"],
    "later stage": ["Later Stage VC", "Expansion / Late Stage"],
    "later stage vc": ["Later Stage VC"],
    "late stage": ["Later Stage VC", "Late Stage Venture", "Expansion / Late Stage"],
    "late stage venture": ["Late Stage Venture"],
    "expansion": ["Expansion / Late Stage", "PE Growth/Expansion"],

    # Growth
    "growth": ["Growth", "PE Growth/Expansion", "Expansion / Late Stage"],
    "growth equity": ["Growth", "PE Growth/Expansion"],
    "pe growth": ["PE Growth/Expansion"],

    # Venture (general)
    "venture": ["Venture (General)", "Early Stage VC", "Later Stage VC"],
    "vc": ["Venture (General)", "Early Stage VC", "Later Stage VC"],

    # Buyout
    "buyout": ["Buyout/LBO", "Buyout", "Management Buyout"],
    "lbo": ["Buyout/LBO"],
    "buyout/lbo": ["Buyout/LBO"],
    "mbo": ["Management Buyout"],
    "management buyout": ["Management Buyout"],
    "mbi": ["Management Buy-In"],
    "management buy-in": ["Management Buy-In"],

    # M&A / strategic
    "m&a": ["Merger/Acquisition", "Add-on", "Acquisition Financing", "Carveout"],
    "merger": ["Merger/Acquisition"],
    "acquisition": ["Merger/Acquisition", "Asset Acquisition", "Acquisition Financing"],
    "add-on": ["Add-on"],
    "carveout": ["Carveout"],
    "corporate divestiture": ["Corporate Divestiture"],
    "divestiture": ["Corporate Divestiture", "Asset Divestiture (Corporate)"],
    "spin-off": ["Spin-Off"],

    # Fund raise
    "fundraise": ["Venture (General)", "Growth", "PE Growth/Expansion", "Early Stage VC",
                  "Later Stage VC"],
    "capital raise": ["Venture (General)", "Growth", "PE Growth/Expansion", "Early Stage VC",
                      "Later Stage VC"],
    "primary": ["Venture (General)", "Growth", "Early Stage VC"],

    # Special situations / distressed
    "distressed": ["Distressed Debt", "Distressed Acquisition", "Turnaround",
                   "Special Situations"],
    "turnaround": ["Turnaround"],
    "special situations": ["Special Situations"],
    "restructuring": ["Bankruptcy: Admin/Reorg", "Recapitalization", "Turnaround"],
    "bankruptcy": ["Bankruptcy: Admin/Reorg", "Bankruptcy: Liquidation"],

    # Debt / credit
    "debt": ["Debt - General", "Mezzanine", "Venture Debt", "Secured Debt", "Senior Debt",
             "Subordinated Debt"],
    "mezzanine": ["Mezzanine"],
    "venture debt": ["Venture Debt"],
    "senior debt": ["Senior Debt"],
    "subordinated debt": ["Subordinated Debt"],
    "convertible": ["Convertible Debt", "Bonds (Convertible)"],
    "convertible debt": ["Convertible Debt"],
    "bridge": ["Bridge"],
    "loan": ["Loan"],
    "working capital": ["Working Capital"],

    # Real estate
    "real estate": ["Real Estate", "Hotel", "Hospital/Healthcare Facility"],

    # Other structures
    "pipe": ["PIPE"],
    "ipo": ["IPO"],
    "spac": ["PIPE"],
    "secondaries": ["Secondaries"],
    "co-investment": ["Co-investment"],
    "fund of funds": ["Fund of Funds"],
    "grant": ["Grant"],
    "project finance": ["Project Financing"],
    "infrastructure": ["Project Financing", "Natural Resources"],
    "joint venture": ["Joint Venture"],
    "recapitalization": ["Recapitalization", "Dividend Recapitalization",
                         "Leveraged Recapitalization"],

    # Corporate / general
    "corporate": ["Corporate", "General Corporate Purpose"],
    "accelerator": ["Accelerator/Incubator"],
    "incubator": ["Accelerator/Incubator"],
    "university spin-out": ["University Spin-Out"],
    "spin-out": ["University Spin-Out"],
}


# ---------------------------------------------------------------------------
# Deal-stage presets (curated lists of investment_types_array values)
# ---------------------------------------------------------------------------

SEED_STAGES: list[str] = [
    "Seed Round",
    "Seed",
    "Angel (individual)",
    "Start-up",
    "Accelerator/Incubator",
]

SERIES_A_STAGES: list[str] = [
    "Early Stage VC",
    "Early Stage",
    "Venture (General)",
]

GROWTH_STAGES: list[str] = [
    "Growth",
    "Later Stage VC",
    "Late Stage Venture",
    "PE Growth/Expansion",
    "Expansion / Late Stage",
    "Venture (General)",
]

BUYOUT_STAGES: list[str] = [
    "Buyout/LBO",
    "Buyout",
    "Management Buyout",
    "Management Buy-In",
    "Merger/Acquisition",
    "Add-on",
    "Carveout",
    "Acquisition Financing",
    "Corporate Divestiture",
    "Asset Acquisition",
    "Public to Private",
]

FUND_RAISE_STAGES: list[str] = [
    "Venture (General)",
    "Early Stage VC",
    "Later Stage VC",
    "Growth",
    "PE Growth/Expansion",
    "Expansion / Late Stage",
    "Seed Round",
    "Seed",
    "Start-up",
]

# Index: preset name → list of investment types
DEAL_STAGE_PRESETS: dict[str, list[str]] = {
    "seed": SEED_STAGES,
    "series_a": SERIES_A_STAGES,
    "growth": GROWTH_STAGES,
    "buyout": BUYOUT_STAGES,
    "fund_raise": FUND_RAISE_STAGES,
}

# Flatten: all known investment type values
ALL_INVESTMENT_TYPES = sorted(set(INVESTMENT_TYPES))


# ---------------------------------------------------------------------------
# Resolution functions
# ---------------------------------------------------------------------------


def resolve_sectors(human_names: list[str]) -> list[str]:
    """Convert human-readable sector names to DB codes.

    Accepts both human names ("energy") and raw DB codes ("cleantech").
    Returns deduplicated list of DB codes for PostgREST overlap filter.
    """
    codes: set[str] = set()
    for name in human_names:
        name_l = name.lower().strip()
        # Check if it's a human-readable key
        if name_l in SECTOR_MAP:
            codes.update(SECTOR_MAP[name_l])
        else:
            # Check if it's already a raw DB code
            if name_l in ALL_SECTOR_CODES:
                codes.add(name_l)
            else:
                # Fuzzy match: check if it's a substring of any key
                for key, vals in SECTOR_MAP.items():
                    if name_l in key:
                        codes.update(vals)
    return sorted(codes)


def resolve_investor_types(human_names: list[str]) -> list[str]:
    """Convert human-readable investor type names to DB values.

    Maps aliases (e.g., "family office") to the full set of matching DB enum
    values (e.g., ["Family Office", "Family Office - Single", "Family Office - Multi"]).

    Also accepts raw DB values (e.g., "Venture Capital") directly.
    Returns deduplicated list of primary_investor_type values.

    Args:
        human_names: List of human-readable investor type names.

    Returns:
        Sorted, deduplicated list of primary_investor_type DB values.
    """
    values: set[str] = set()
    investor_types_lower = {t.lower(): t for t in INVESTOR_TYPES}

    for name in human_names:
        name_l = name.lower().strip()
        # Exact alias match
        if name_l in _INVESTOR_TYPE_MAP:
            values.update(_INVESTOR_TYPE_MAP[name_l])
        # Pass-through: already a valid DB value (case-insensitive)
        elif name_l in investor_types_lower:
            values.add(investor_types_lower[name_l])
        else:
            # Fuzzy match: check if input is a substring of any alias key
            for key, vals in _INVESTOR_TYPE_MAP.items():
                if name_l in key:
                    values.update(vals)

    return sorted(values)


def resolve_investment_types(human_names: list[str]) -> list[str]:
    """Convert human-readable deal stage / investment type names to DB values.

    Maps aliases (e.g., "seed") to investment_types_array DB values used in
    the preferred_investment_types ilike filter or investment_types_array overlap.

    Also accepts preset names (e.g., "buyout_stages") and raw DB values.

    Args:
        human_names: List of human-readable investment type names or preset names.

    Returns:
        Sorted, deduplicated list of investment_types_array DB values.
    """
    values: set[str] = set()
    investment_types_lower = {t.lower(): t for t in INVESTMENT_TYPES}

    for name in human_names:
        name_l = name.lower().strip()

        # Check preset names (e.g., "seed_stages", "buyout_stages")
        preset_key = name_l.removesuffix("_stages")
        if preset_key in DEAL_STAGE_PRESETS:
            values.update(DEAL_STAGE_PRESETS[preset_key])
        # Exact alias match
        elif name_l in _INVESTMENT_TYPE_MAP:
            values.update(_INVESTMENT_TYPE_MAP[name_l])
        # Pass-through: already a valid DB value (case-insensitive)
        elif name_l in investment_types_lower:
            values.add(investment_types_lower[name_l])
        else:
            # Fuzzy match: check if input is a substring of any alias key
            for key, vals in _INVESTMENT_TYPE_MAP.items():
                if name_l in key:
                    values.update(vals)

    return sorted(values)
