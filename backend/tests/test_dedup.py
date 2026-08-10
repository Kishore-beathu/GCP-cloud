"""Near-duplicate detection, and the asymmetry it is tuned around.

A missed merge shows a duplicate in the feed. A wrong merge hides a real event
behind an unrelated one — it is not scored, not alerted on, and not counted by
the backtester. So every ambiguous case here should resolve to "not a
duplicate".
"""

from __future__ import annotations

from app.services.dedup import is_duplicate, signature, similarity


def test_identical_headlines_match():
    headline = "FDA approves Merck KEYTRUDA for early-stage lung cancer"

    assert is_duplicate(headline, headline)


def test_wire_rewording_of_one_release_matches():
    """The same release, as three wires actually rewrite it."""
    original = "Merck Announces FDA Approval of KEYTRUDA for Early-Stage Lung Cancer"
    rewritten = "FDA Approval of KEYTRUDA in Early-Stage Lung Cancer Announced by Merck"
    prefixed = "PRESS RELEASE: Merck announces FDA approval of KEYTRUDA, early-stage lung cancer"

    assert is_duplicate(original, rewritten)
    assert is_duplicate(original, prefixed)


def test_different_events_for_one_company_do_not_match():
    approval = "Merck announces FDA approval of KEYTRUDA for early-stage lung cancer"
    offering = "Merck prices $2 billion senior notes offering"

    assert not is_duplicate(approval, offering)


def test_opposite_outcomes_do_not_match():
    """The costliest possible wrong merge: an approval hidden behind a rejection."""
    approved = "FDA approves Sarepta gene therapy for Duchenne muscular dystrophy"
    rejected = "FDA rejects Sarepta gene therapy for Duchenne muscular dystrophy"

    assert not is_duplicate(approved, rejected)


def test_same_event_different_companies_do_not_match():
    pfizer = "Pfizer reports positive phase 3 results in atopic dermatitis"
    lilly = "Lilly reports positive phase 3 results in atopic dermatitis"

    assert not is_duplicate(pfizer, lilly)


def test_short_headlines_need_to_be_identical():
    """Two short headlines share words by accident; demand exact agreement."""
    assert is_duplicate("Pfizer up 3%", "Pfizer up 3%")
    assert not is_duplicate("Pfizer up 3%", "Pfizer down 3%")


def test_signature_drops_boilerplate_and_short_words():
    tokens = signature("PRESS RELEASE: The Company Announces Its Q3 Revenue Results")

    assert "press" not in tokens
    assert "announces" not in tokens
    assert "the" not in tokens
    assert "revenue" in tokens


def test_similarity_is_bounded_and_symmetric():
    left = signature("FDA approves Merck KEYTRUDA lung cancer")
    right = signature("Merck KEYTRUDA approved by FDA for lung cancer")

    assert 0.0 <= similarity(left, right) <= 1.0
    assert similarity(left, right) == similarity(right, left)
    assert similarity(left, frozenset()) == 0.0


# --- Outcome polarity -------------------------------------------------------
# Overlap alone cannot separate these: a single verb carries the entire
# meaning, and the remaining seven words are identical.


def test_trial_success_and_failure_do_not_match():
    hit = "Novartis phase 3 trial met its primary endpoint in heart failure"
    miss = "Novartis phase 3 trial failed to meet its primary endpoint in heart failure"

    assert not is_duplicate(hit, miss)


def test_negated_success_is_read_as_failure():
    """The 'meet' inside 'did not meet' must not register as a positive."""
    hit = "Biogen study met the primary endpoint in Alzheimer's disease"
    miss = "Biogen study did not meet the primary endpoint in Alzheimer's disease"

    assert not is_duplicate(hit, miss)


def test_guidance_raised_and_cut_do_not_match():
    up = "Pfizer raises full-year revenue guidance on strong oncology demand"
    down = "Pfizer cuts full-year revenue guidance on strong oncology demand"

    assert not is_duplicate(up, down)


def test_polarity_guard_does_not_block_a_genuine_copy():
    """Both copies carrying the same outcome must still merge."""
    a = "FDA approves Lilly tirzepatide for obstructive sleep apnea"
    b = "Lilly tirzepatide approved by FDA for obstructive sleep apnea"

    assert is_duplicate(a, b)


def test_a_heavy_rewrite_is_left_unmerged_on_purpose():
    """Where the threshold sits, stated as behaviour rather than implied.

    These are the same event, written by two desks: "approves" against
    "cleared", and one adds a geography. Overlap is 0.63, below the 0.75 bar,
    so the feed shows two rows.

    That is the intended trade. Loosening the bar far enough to catch synonym
    rewrites also catches genuinely different stories about one company on one
    day, and a wrong merge deletes an event from the feed, the alerts and the
    backtest, whereas this one only shows a duplicate.
    """
    a = "FDA approves Lilly tirzepatide for obstructive sleep apnea"
    b = "Lilly tirzepatide cleared for obstructive sleep apnea in the US"

    assert not is_duplicate(a, b)
    # It is close, though: worth revisiting if the feed fills with pairs.
    assert 0.55 < similarity(signature(a), signature(b)) < 0.75
