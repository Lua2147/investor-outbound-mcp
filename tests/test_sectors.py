"""Tests for src/sectors.py — sector, investor type, and investment type resolution."""
import pytest

from src.sectors import (
    ALL_INVESTMENT_TYPES,
    ALL_SECTOR_CODES,
    BUYOUT_STAGES,
    DEAL_STAGE_PRESETS,
    FUND_RAISE_STAGES,
    GROWTH_STAGES,
    INVESTOR_TYPES,
    INVESTMENT_TYPES,
    SEED_STAGES,
    SERIES_A_STAGES,
    resolve_investment_types,
    resolve_investor_types,
    resolve_sectors,
)


# ---------------------------------------------------------------------------
# resolve_sectors
# ---------------------------------------------------------------------------


class TestResolveSectors:
    def test_exact_human_key_returns_all_codes(self):
        codes = resolve_sectors(["energy"])
        assert "energy" in codes
        assert "cleantech" in codes
        assert "green_energy" in codes

    def test_raw_db_code_passes_through(self):
        codes = resolve_sectors(["cleantech"])
        assert "cleantech" in codes

    def test_fuzzy_match_on_partial_key(self):
        # "fin" is a substring of "financial services" and "financial investments"
        codes = resolve_sectors(["fin"])
        # Should return some financial sector codes
        assert len(codes) > 0

    def test_multiple_sectors_deduplicated(self):
        # "energy" and "cleantech" share "cleantech" — it should appear once
        codes_combined = resolve_sectors(["energy", "cleantech"])
        codes_energy = resolve_sectors(["energy"])
        assert codes_combined.count("cleantech") == 1
        assert set(codes_energy).issubset(set(codes_combined))

    def test_unknown_input_returns_empty(self):
        codes = resolve_sectors(["zzz_nonexistent_sector_xyz"])
        assert codes == []

    def test_case_insensitive(self):
        assert resolve_sectors(["Energy"]) == resolve_sectors(["energy"])
        assert resolve_sectors(["REAL ESTATE"]) == resolve_sectors(["real estate"])

    def test_all_sector_codes_covers_known_values(self):
        assert "fin_services" in ALL_SECTOR_CODES
        assert "ai_ml" in ALL_SECTOR_CODES
        assert "real_estate" in ALL_SECTOR_CODES


# ---------------------------------------------------------------------------
# resolve_investor_types
# ---------------------------------------------------------------------------


class TestResolveInvestorTypes:
    def test_family_office_expands_to_all_variants(self):
        types = resolve_investor_types(["family office"])
        assert "Family Office" in types
        assert "Family Office - Single" in types
        assert "Family Office - Multi" in types

    def test_pe_expands_to_multiple_values(self):
        types = resolve_investor_types(["pe"])
        assert "PE/Buyout" in types
        assert "Private Equity" in types

    def test_raw_db_value_passes_through(self):
        types = resolve_investor_types(["Venture Capital"])
        assert "Venture Capital" in types

    def test_alias_vc_resolves(self):
        types = resolve_investor_types(["vc"])
        assert "Venture Capital" in types

    def test_ria_alias_resolves(self):
        types = resolve_investor_types(["ria"])
        assert "Wealth Management/RIA" in types

    def test_multiple_types_deduplicated(self):
        # "vc" and "venture capital" both map to "Venture Capital"
        types = resolve_investor_types(["vc", "venture capital"])
        assert types.count("Venture Capital") == 1

    def test_unknown_input_returns_empty(self):
        types = resolve_investor_types(["zzz_unknown_investor_type"])
        assert types == []

    def test_investor_types_list_completeness(self):
        # Verify the enum list contains all values from the 1K sample
        assert "Wealth Management/RIA" in INVESTOR_TYPES
        assert "Financial Advisor" in INVESTOR_TYPES
        assert "Wealth Manager" in INVESTOR_TYPES
        assert "Family Office - Multi" in INVESTOR_TYPES
        assert "Private Sector Pension Fund" in INVESTOR_TYPES
        assert "Public Pension Fund" in INVESTOR_TYPES
        assert "Fund of Hedge Funds Manager" in INVESTOR_TYPES
        assert "Real Estate Fund of Funds Manager" in INVESTOR_TYPES
        assert "Special Purpose Acquisition Company (SPAC)" in INVESTOR_TYPES
        assert len(INVESTOR_TYPES) >= 48

    def test_pension_alias_returns_both_variants(self):
        types = resolve_investor_types(["pension"])
        assert "Private Sector Pension Fund" in types
        assert "Public Pension Fund" in types

    def test_case_insensitive_passthrough(self):
        # "venture capital" lowercase should resolve even via pass-through path
        types = resolve_investor_types(["venture capital"])
        assert "Venture Capital" in types


# ---------------------------------------------------------------------------
# resolve_investment_types
# ---------------------------------------------------------------------------


class TestResolveInvestmentTypes:
    def test_seed_alias_expands_to_seed_types(self):
        types = resolve_investment_types(["seed"])
        assert "Seed Round" in types
        assert "Seed" in types
        assert "Start-up" in types

    def test_buyout_alias_expands_correctly(self):
        types = resolve_investment_types(["buyout"])
        assert "Buyout/LBO" in types
        assert "Buyout" in types
        assert "Management Buyout" in types

    def test_raw_db_value_passes_through(self):
        types = resolve_investment_types(["Seed Round"])
        assert "Seed Round" in types

    def test_growth_alias_resolves(self):
        types = resolve_investment_types(["growth"])
        assert "Growth" in types
        assert "PE Growth/Expansion" in types

    def test_manda_alias_resolves(self):
        types = resolve_investment_types(["m&a"])
        assert "Merger/Acquisition" in types
        assert "Add-on" in types

    def test_unknown_input_returns_empty(self):
        types = resolve_investment_types(["zzz_unknown_deal_type"])
        assert types == []

    def test_multiple_inputs_deduplicated(self):
        # "seed" and "seed round" both include "Seed Round"
        types = resolve_investment_types(["seed", "seed round"])
        assert types.count("Seed Round") == 1

    def test_investment_types_list_completeness(self):
        assert len(INVESTMENT_TYPES) >= 77
        assert "Seed Round" in INVESTMENT_TYPES
        assert "Buyout/LBO" in INVESTMENT_TYPES
        assert "Early Stage VC" in INVESTMENT_TYPES
        assert "Venture (General)" in INVESTMENT_TYPES
        assert "Distressed Debt" in INVESTMENT_TYPES
        assert "CLO" in INVESTMENT_TYPES


# ---------------------------------------------------------------------------
# Deal-stage presets
# ---------------------------------------------------------------------------


class TestDealStagePresets:
    def test_seed_stages_preset_non_empty(self):
        assert len(SEED_STAGES) >= 4
        assert "Seed Round" in SEED_STAGES
        assert "Angel (individual)" in SEED_STAGES

    def test_series_a_stages_preset_non_empty(self):
        assert len(SERIES_A_STAGES) >= 2
        assert "Early Stage VC" in SERIES_A_STAGES

    def test_growth_stages_preset_non_empty(self):
        assert len(GROWTH_STAGES) >= 3
        assert "Growth" in GROWTH_STAGES
        assert "PE Growth/Expansion" in GROWTH_STAGES

    def test_buyout_stages_preset_non_empty(self):
        assert len(BUYOUT_STAGES) >= 5
        assert "Buyout/LBO" in BUYOUT_STAGES
        assert "Management Buyout" in BUYOUT_STAGES
        assert "Merger/Acquisition" in BUYOUT_STAGES

    def test_fund_raise_stages_preset_non_empty(self):
        assert len(FUND_RAISE_STAGES) >= 5
        assert "Venture (General)" in FUND_RAISE_STAGES

    def test_preset_names_resolve_via_function(self):
        # resolve_investment_types should accept preset names directly
        types = resolve_investment_types(["seed_stages"])
        assert set(SEED_STAGES).issubset(set(types))

        types = resolve_investment_types(["buyout_stages"])
        assert set(BUYOUT_STAGES).issubset(set(types))

    def test_deal_stage_presets_dict_has_all_keys(self):
        assert "seed" in DEAL_STAGE_PRESETS
        assert "series_a" in DEAL_STAGE_PRESETS
        assert "growth" in DEAL_STAGE_PRESETS
        assert "buyout" in DEAL_STAGE_PRESETS
        assert "fund_raise" in DEAL_STAGE_PRESETS

    def test_all_preset_values_are_valid_db_values(self):
        """Every value in every preset must be a real investment_types_array DB value."""
        for preset_name, preset_vals in DEAL_STAGE_PRESETS.items():
            for val in preset_vals:
                assert val in ALL_INVESTMENT_TYPES, (
                    f"Preset '{preset_name}' contains invalid DB value: '{val}'"
                )

    def test_preset_via_base_name(self):
        # "buyout" (without _stages suffix) should also work as a preset key
        types = resolve_investment_types(["buyout"])
        # "buyout" is also an alias, but BUYOUT_STAGES values should be present
        assert "Buyout/LBO" in types
