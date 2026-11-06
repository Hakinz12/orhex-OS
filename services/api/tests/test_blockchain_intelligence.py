from app.services.blockchain_intelligence import analyze_wallet


def test_wallet_intelligence_reports_activity_without_comparing_token_units():
    result = analyze_wallet(
        "0xabc", "mainnet",
        [
            {"coin_type": "0x2::sui::SUI", "symbol": "SUI", "amount": "95", "decimals": 9},
            {"coin_type": "0xcoin", "symbol": "ALT", "amount": "5", "decimals": 2},
        ],
        [{"digest": "tx1", "status": "failure", "timestamp_ms": 1},
         {"digest": "tx2", "status": "success", "timestamp_ms": 2}],
        123,
    )
    assert result["checkpoint"] == 123
    assert result["activity"]["failed"] == 1
    assert result["activity"]["successful"] == 1
    assert result["portfolio"]["known_token_count"] == 2
    assert "known_amount_total" not in result["portfolio"]
    assert "largest_holding_share_percent" not in result["portfolio"]
    assert result["snapshot_consistency"] == "independent_provider_reads"
    assert {item["code"] for item in result["insights"]} == {"failed_transactions"}
    assert any("not summed" in item for item in result["limitations"])


def test_wallet_intelligence_does_not_invent_valuation_or_flag_empty_activity_as_clean():
    result = analyze_wallet("0xabc", "testnet", [], [], 0)
    assert result["portfolio"]["known_token_count"] == 0
    assert result["portfolio"]["holdings"] == []
    assert "usd_value" not in result["portfolio"]
    assert result["activity"]["unknown_status"] == 0
    assert result["insights"][0]["code"] == "limited_activity_data"


def test_wallet_intelligence_keeps_token_units_separate_and_skips_malformed_values():
    result = analyze_wallet(
        "0xabc",
        "mainnet",
        [
            {"coin_type": "token-a", "amount": "1", "decimals": 9},
            {"coin_type": "token-b", "amount": "1", "decimals": 0},
            {"coin_type": "token-c", "amount": "0", "decimals": 18},
            {"coin_type": "token-d", "amount": "NaN", "decimals": 6},
            {"coin_type": "token-e", "amount": "-2", "decimals": 2},
            {"coin_type": "token-f", "amount": "not-a-number", "decimals": 4},
            {"amount": "4", "decimals": 2},
            None,
        ],
        [],
        0,
    )
    assert result["portfolio"]["known_token_count"] == 3
    assert {item["coin_type"]: item["amount"] for item in result["portfolio"]["holdings"]} == {
        "token-a": "1",
        "token-b": "1",
        "token-c": "0",
    }
