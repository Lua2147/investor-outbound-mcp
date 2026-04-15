"""Analytics tools — Tools 26–27.

Tools:
    io_sector_landscape       — Type/geo/check-size breakdown for a sector.
    io_check_size_distribution — Check size histogram for sector + investor type.

All tools are registered by calling ``register(mcp, client)`` — server.py auto-discovers this.

Design notes:
- check_size_min/max are stored as MILLIONS USD in the DB. Bucket labels reflect this.
- Sectors resolved via resolve_sectors() (human names → DB codes).
- Investor types resolved via resolve_investor_types() (human names → DB enum values).
- stats_response() is used for all analytics responses (no pagination, no next_actions).
- Percentiles computed in Python over the fetched result set (no DB-side aggregation).
- AUM (capital_under_management) is a free-text field — not aggregated here.
- Histograms use closed lower bound, open upper bound per bucket except the top bucket.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.client import IOAuthError, IOClient, IOQueryError, IOTransientError, QueryBuilder
from src.helpers import error_response, stats_response
from src.sectors import resolve_investor_types, resolve_sectors

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Check-size histogram buckets — values in MILLIONS USD
# Buckets: <$1M, $1–5M, $5–25M, $25–100M, $100M–$1B, $1B+
# ---------------------------------------------------------------------------

_BUCKETS: list[tuple[str, float, float]] = [
    ("<$1M", 0.0, 1.0),
    ("$1–5M", 1.0, 5.0),
    ("$5–25M", 5.0, 25.0),
    ("$25–100M", 25.0, 100.0),
    ("$100M–$1B", 100.0, 1_000.0),
    ("$1B+", 1_000.0, float("inf")),
]

# Maximum investors to fetch per analytics query.
# Large enough to give reliable aggregate stats without overwhelming the event loop.
_ANALYTICS_FETCH_LIMIT = 5_000


def _bucket_label(value_millions: float) -> str:
    """Return the bucket label for a check_size value (in millions)."""
    for label, lo, hi in _BUCKETS:
        if lo <= value_millions < hi:
            return label
    # Should only happen if value is exactly inf or negative — defensive fallback
    return "$1B+"


def _compute_percentiles(values: list[float]) -> dict[str, float | None]:
    """Compute p25, p50, p75 from a sorted or unsorted list of floats.

    Returns None for each percentile when the list is empty.
    """
    if not values:
        return {"p25": None, "p50": None, "p75": None}

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def _percentile(p: float) -> float:
        idx = (p / 100) * (n - 1)
        lo = int(idx)
        hi = lo + 1
        if hi >= n:
            return sorted_vals[lo]
        frac = idx - lo
        return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])

    return {
        "p25": round(_percentile(25), 2),
        "p50": round(_percentile(50), 2),
        "p75": round(_percentile(75), 2),
    }


def register(mcp: FastMCP, client: IOClient) -> None:
    """Register all 2 Analytics tools on the FastMCP instance."""

    # ------------------------------------------------------------------
    # Tool 26: io_sector_landscape
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_sector_landscape(
        sector: str,
        include_acquired: bool = False,
    ) -> str:
        """Type/geography/check-size breakdown for a sector.

        Queries all investors with sectors_array overlap for the given sector,
        then aggregates:
        - Count by primary_investor_type (all types)
        - Count by hq_country_generated (top 10 countries)
        - Check size histogram across 6 buckets: <$1M, $1–5M, $5–25M,
          $25–100M, $100M–$1B, $1B+
        - Top 10 firms by contact_count

        check_size values are stored as MILLIONS in the DB. Bucket labels
        are rendered in human-readable dollar terms.

        Args:
            sector: Human-readable sector name (e.g. "energy", "fintech",
                "healthcare"). Resolved to DB sector code(s) via the sector map.
                Pass the raw DB code (e.g. "clean_tech") to bypass resolution.
            include_acquired: If True, include investors with status
                "Acquired/Merged". Defaults to False.

        Returns:
            JSON string:
            ``{
                "data": {
                    "sector": "energy",
                    "total_investors": 1234,
                    "by_investor_type": {"Venture Capital": 312, ...},
                    "by_country_top10": {"United States": 480, ...},
                    "check_size_histogram": {"<$1M": 45, "$1–5M": 210, ...},
                    "top_firms_by_contacts": [
                        {"investors": "...", "primary_investor_type": "...",
                         "contact_count": 42}, ...
                    ]
                },
                "summary": "..."
            }``
        """
        db_codes = resolve_sectors([sector])
        if not db_codes:
            return error_response(
                "VALIDATION_ERROR",
                f"Could not resolve sector {sector!r} to a DB code. "
                "Use a recognised sector name such as 'energy', 'fintech', or 'healthcare'.",
            )

        try:
            qb = (
                QueryBuilder("investors")
                .select(
                    "id,investors,primary_investor_type,"
                    "hq_country_generated,check_size_min,check_size_max,"
                    "contact_count,investor_status"
                )
                .ov("sectors_array", db_codes)
            )

            if not include_acquired:
                qb = qb.neq("investor_status", "Acquired/Merged")

            qb = qb.order("contact_count", ascending=False).limit(_ANALYTICS_FETCH_LIMIT)

            rows, total = await client.query(qb)

        except IOAuthError as exc:
            logger.warning("io_sector_landscape: auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed — check IO_EMAIL/IO_PASSWORD")
        except IOQueryError as exc:
            logger.error("io_sector_landscape: query error: %s", exc)
            return error_response("QUERY_ERROR", "Database query failed", str(exc))
        except IOTransientError as exc:
            logger.error("io_sector_landscape: transient error: %s", exc)
            return error_response("SERVER_ERROR", "Transient database error — retry the request")
        except Exception:
            logger.exception("io_sector_landscape: unexpected error")
            return error_response("SERVER_ERROR", "Unexpected server error")

        # --- Aggregate: by_investor_type ---
        by_type: dict[str, int] = {}
        for row in rows:
            t = row.get("primary_investor_type") or "Unknown"
            by_type[t] = by_type.get(t, 0) + 1
        by_type_sorted = dict(sorted(by_type.items(), key=lambda kv: kv[1], reverse=True))

        # --- Aggregate: by_country_top10 ---
        by_country: dict[str, int] = {}
        for row in rows:
            c = row.get("hq_country_generated") or "Unknown"
            by_country[c] = by_country.get(c, 0) + 1
        by_country_top10 = dict(
            sorted(by_country.items(), key=lambda kv: kv[1], reverse=True)[:10]
        )

        # --- Aggregate: check_size_histogram (on check_size_min) ---
        histogram: dict[str, int] = {label: 0 for label, _, _ in _BUCKETS}
        for row in rows:
            csm = row.get("check_size_min")
            if csm is not None:
                try:
                    label = _bucket_label(float(csm))
                    histogram[label] += 1
                except (TypeError, ValueError):
                    pass

        # --- Aggregate: top_firms_by_contacts ---
        top_firms = [
            {
                "investors": r.get("investors"),
                "primary_investor_type": r.get("primary_investor_type"),
                "contact_count": r.get("contact_count") or 0,
            }
            for r in rows
            if (r.get("contact_count") or 0) > 0
        ]
        # Already ordered by contact_count desc from the query
        top_firms_top10 = top_firms[:10]

        result_total = total if total is not None else len(rows)

        data: dict[str, Any] = {
            "sector": sector,
            "resolved_db_codes": db_codes,
            "total_investors": result_total,
            "fetched_for_aggregation": len(rows),
            "by_investor_type": by_type_sorted,
            "by_country_top10": by_country_top10,
            "check_size_histogram": histogram,
            "top_firms_by_contacts": top_firms_top10,
        }

        summary = (
            f"Sector '{sector}': {result_total} investors total — "
            f"{len(by_type_sorted)} investor types, "
            f"top country: {next(iter(by_country_top10), 'n/a')}"
        )

        return stats_response(data, summary)

    # ------------------------------------------------------------------
    # Tool 27: io_check_size_distribution
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_check_size_distribution(
        sector: str,
        investor_type: str | None = None,
        include_acquired: bool = False,
    ) -> str:
        """Check size histogram and percentiles for a sector + optional investor type.

        Queries investors matching the sector (and optionally investor type),
        then returns:
        - Histogram of check_size_min values across 6 buckets
        - p25 / p50 / p75 percentiles (in millions USD)
        - Count of investors with / without check size data

        check_size values are stored as MILLIONS in the DB. All returned values
        (bucket labels, percentiles) are in millions USD unless stated otherwise.

        Args:
            sector: Human-readable sector name (e.g. "energy", "fintech").
                Resolved to DB sector code(s). Pass raw DB code to bypass resolution.
            investor_type: Optional human-readable investor type filter (e.g.
                "family office", "vc", "pe"). Resolved to DB enum values. When
                provided, only matching investor types are included.
            include_acquired: If True, include investors with status
                "Acquired/Merged". Defaults to False.

        Returns:
            JSON string:
            ``{
                "data": {
                    "sector": "energy",
                    "investor_type": "Venture Capital",
                    "total_investors": 412,
                    "with_check_size_data": 280,
                    "without_check_size_data": 132,
                    "histogram": {"<$1M": 12, "$1–5M": 55, ...},
                    "percentiles_millions": {"p25": 5.0, "p50": 15.0, "p75": 50.0}
                },
                "summary": "..."
            }``
        """
        db_codes = resolve_sectors([sector])
        if not db_codes:
            return error_response(
                "VALIDATION_ERROR",
                f"Could not resolve sector {sector!r} to a DB code. "
                "Use a recognised sector name such as 'energy', 'fintech', or 'healthcare'.",
            )

        # Resolve investor type if provided
        resolved_types: list[str] = []
        if investor_type:
            resolved_types = resolve_investor_types([investor_type])
            if not resolved_types:
                return error_response(
                    "VALIDATION_ERROR",
                    f"Could not resolve investor_type {investor_type!r} to a DB value. "
                    "Examples: 'venture capital', 'family office', 'pe', 'hedge fund'.",
                )

        try:
            qb = (
                QueryBuilder("investors")
                .select("id,primary_investor_type,check_size_min,check_size_max,investor_status")
                .ov("sectors_array", db_codes)
            )

            if resolved_types:
                qb = qb.in_("primary_investor_type", resolved_types)

            if not include_acquired:
                qb = qb.neq("investor_status", "Acquired/Merged")

            qb = qb.limit(_ANALYTICS_FETCH_LIMIT)

            rows, total = await client.query(qb)

        except IOAuthError as exc:
            logger.warning("io_check_size_distribution: auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed — check IO_EMAIL/IO_PASSWORD")
        except IOQueryError as exc:
            logger.error("io_check_size_distribution: query error: %s", exc)
            return error_response("QUERY_ERROR", "Database query failed", str(exc))
        except IOTransientError as exc:
            logger.error("io_check_size_distribution: transient error: %s", exc)
            return error_response("SERVER_ERROR", "Transient database error — retry the request")
        except Exception:
            logger.exception("io_check_size_distribution: unexpected error")
            return error_response("SERVER_ERROR", "Unexpected server error")

        # Build histogram and collect values for percentile calculation
        histogram: dict[str, int] = {label: 0 for label, _, _ in _BUCKETS}
        check_size_values: list[float] = []
        with_data = 0
        without_data = 0

        for row in rows:
            csm = row.get("check_size_min")
            if csm is not None:
                try:
                    val = float(csm)
                    label = _bucket_label(val)
                    histogram[label] += 1
                    check_size_values.append(val)
                    with_data += 1
                except (TypeError, ValueError):
                    without_data += 1
            else:
                without_data += 1

        percentiles = _compute_percentiles(check_size_values)
        result_total = total if total is not None else len(rows)

        data: dict[str, Any] = {
            "sector": sector,
            "resolved_db_codes": db_codes,
            "investor_type": investor_type,
            "resolved_investor_types": resolved_types if resolved_types else None,
            "total_investors": result_total,
            "fetched_for_aggregation": len(rows),
            "with_check_size_data": with_data,
            "without_check_size_data": without_data,
            "histogram": histogram,
            "percentiles_millions": percentiles,
        }

        type_clause = f" ({investor_type})" if investor_type else ""
        median = percentiles["p50"]
        median_str = f"${median}M median" if median is not None else "no check size data"
        summary = (
            f"Check size distribution for '{sector}'{type_clause}: "
            f"{result_total} investors, {with_data} with check size data, {median_str}"
        )

        return stats_response(data, summary)
