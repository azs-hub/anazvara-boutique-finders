"""Evidence scores for Anazvara stockist fit.

Scores count observable phrases. Missing evidence stays UNKNOWN.
UNKNOWN is not treated as NO.
"""

from __future__ import annotations

import re
from typing import Any

from geography import ExpectedPlace, parse_expected_place, verify_geography

YES_NO_UNKNOWN = {"YES", "NO", "UNKNOWN"}

# (pattern, points, evidence label)
PHYSICAL_SIGNALS: tuple[tuple[str, int, str], ...] = (
    (r"\b\d{1,4}[,\s]+[A-Za-z].{0,40}\b(?:road|rd|street|st|marg|lane|avenue|nagar)\b", 5, "explicit physical address"),
    (r"\b(?:shop|store)\s+(?:no|number|#)\s*\d+", 5, "explicit physical address"),
    (r"\b[1-9]\d{5}\b", 5, "explicit physical address"),
    (r"\b(?:our\s+store|store\s+location|visit\s+our\s+(?:store|boutique)|find\s+us\s+at)\b", 4, "dedicated store page"),
    (r"\b(?:google\s+maps|maps\.google|goo\.gl/maps)\b", 3, "maps listing"),
    (r"\b(?:opening\s+hours|store\s+hours|open\s+(?:daily|today)|mon(?:day)?\s*[-–]\s*sun)", 2, "opening hours"),
    (r"\b(?:visit\s+us|walk[\s-]in|come\s+visit)\b", 2, "visit us"),
    (r"\b(?:store\s+photographs|inside\s+the\s+(?:store|boutique)|boutique\s+interior)\b", 2, "store photographs"),
    (r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b", 1, "phone number"),
)
MULTI_BRAND_SIGNALS: tuple[tuple[str, int, str], ...] = (
    (r"\b(?:stocks?|carries|carrying|home\s+to)\s+(?:multiple|many|several|\d{2,})\s+(?:designers?|brands?|labels?)", 6, "stocks multiple designers"),
    (r"\b(?:designers?|brands?|labels?)\s+we\s+(?:carry|stock)\b", 4, "designer list"),
    (r"\bcurated\s+(?:selection|edit|collection|store)\b", 3, "curated selection"),
    (r"\bindependent\s+designers?\b", 3, "independent designers"),
    (r"\bmultiple\s+labels?\b", 2, "multiple labels"),
    (r"\bdesigner\s+collective\b", 2, "designer collective"),
    (r"\bconcept[\s-]store\b", 1, "concept store positioning"),
)
CONCEPT_SIGNALS: tuple[tuple[str, int, str], ...] = (
    (r"\bconcept[\s-]store\b", 4, "concept store"),
    (r"\bcurated\s+(?:fashion|lifestyle|store|boutique)\b", 3, "curated store"),
    (r"\bdesigner\s+collective\b", 3, "designer collective"),
    (r"\blifestyle\s+store\b", 2, "lifestyle store"),
)
FIT_SIGNALS: tuple[tuple[str, int, str], ...] = (
    (r"\b(?:women'?s|womens)\s+(?:contemporary|fashion|clothing|wear)\b", 4, "women's contemporary fashion"),
    (r"\bcontemporary\s+(?:women'?s\s+)?(?:fashion|clothing)\b", 4, "women's contemporary fashion"),
    (r"\bindependent\s+designers?\b", 3, "independent designers"),
    (r"\b(?:slow[\s-]fashion|sustainable\s+fashion|conscious\s+fashion)\b", 3, "slow or sustainable fashion"),
    (r"\b(?:linen|natural\s+fabrics?|handloom\s+cotton|organic\s+cotton)\b", 2, "natural fabrics"),
    (r"\b(?:indian\s+craft|craftsmanship|handcrafted|artisanal)\b", 2, "Indian craftsmanship"),
    (r"\bresort(?:\s+and\s+vacation)?\s+fashion\b|\bvacation\s+wear\b", 2, "resort fashion"),
    (r"\b(?:curated|premium)\s+(?:boutique|fashion|edit|positioning|store)\b", 2, "curated premium positioning"),
)
FASHION_SIGNALS: tuple[tuple[str, int, str], ...] = (
    (r"\b(?:women'?s|womens)\s+(?:\w+\s+){0,3}(?:fashion|clothing|wear|boutique)\b", 4, "women's fashion"),
    (r"\b(?:fashion|clothing)\s+boutique\b", 3, "fashion boutique"),
    (r"\b(?:designer\s+(?:wear|clothing|boutique|store)|contemporary\s+fashion)\b", 3, "designer fashion"),
    (r"\b(?:apparel|ready[\s-]to[\s-]wear|rtw)\b", 2, "apparel"),
)

# Positive evidence that the business is unsuitable. Absence is UNKNOWN.
EXCLUSION_SIGNALS: tuple[tuple[str, str], ...] = (
    (r"\b(?:online[\s-]only|online[\s-]exclusive|no\s+physical\s+(?:store|shop))\b", "online-only"),
    (r"\b(?:wholesale[\s-]only|wholesaler\s+only|b2b\s+only)\b", "wholesale-only"),
    (r"\b(?:only\s+our\s+(?:own\s+)?(?:brand|label)|own[\s-]label\s+only|mono[\s-]brand|we\s+only\s+sell\s+our\s+own)\b", "own-brand-only"),
    (r"\b(?:bridal[\s-]only|only\s+bridal|wedding\s+lehenga\s+specialist)\b", "bridal-only"),
    (r"\b(?:saree[\s-]only|only\s+sarees|sarees\s+exclusively)\b", "saree-only"),
    (r"\b(?:tailoring[\s-]only|only\s+tailoring|alterations\s+only)\b", "tailoring-only"),
    (r"\b(?:accessories[\s-]only|jewellery\s+only|jewelry\s+only)\b", "accessories-only"),
    (r"\b(?:mass[\s-]market|fast\s+fashion\s+chain)\b", "mass-market"),
    (r"\b(?:real\s+estate|restaurant|salon\s+only|travel\s+agency|not\s+a\s+(?:fashion\s+)?retailer)\b", "unrelated"),
)

RETAIL_MODEL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bmulti[\s-]brand\b", "multi-brand"),
    (r"\bmulti[\s-]designer\b|\bstocks?\s+(?:multiple|many)\s+designers?\b", "multi-brand"),
    (r"\bconcept[\s-]store\b", "concept store"),
    (r"\bdesigner\s+collective\b", "designer collective"),
    (r"\bcurated\b", "curated store"),
    (r"\blifestyle\s+store\b", "lifestyle store"),
    (r"\bdesigner\s+boutique\b", "designer boutique"),
    (r"\bresort\s+boutique\b|\bhotel\s+boutique\b", "resort boutique"),
    (r"\b(?:only\s+our\s+(?:own\s+)?brand|own[\s-]label\s+only)\b", "own-brand only"),
)


def _score(text: str, signals: tuple[tuple[str, int, str], ...]) -> tuple[int, list[str]]:
    total = 0
    evidence: list[str] = []
    for pattern, points, label in signals:
        if re.search(pattern, text, re.IGNORECASE):
            total += points
            if label not in evidence:
                evidence.append(label)
    return total, evidence


def _exclusions(text: str) -> list[str]:
    found: list[str] = []
    for pattern, label in EXCLUSION_SIGNALS:
        if re.search(pattern, text, re.IGNORECASE) and label not in found:
            found.append(label)
    return found


def _retail_model(text: str, multi_score: int, concept_score: int) -> str:
    for pattern, label in RETAIL_MODEL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    if multi_score >= 4:
        return "multi-brand"
    if concept_score >= 3:
        return "concept store"
    return "unknown"


def _band_fit(score: int) -> str:
    if score >= 6:
        return "HIGH"
    if score >= 3:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "UNKNOWN"


def _tri(yes: bool, no: bool) -> str:
    if no and not yes:
        return "NO"
    if yes:
        return "YES"
    return "UNKNOWN"


def assess_stockist_fit(
    text: str,
    *,
    business_name: str | None = None,
    city: str | None = None,
    address: str | None = None,
    website: str | None = None,
    instagram: str | None = None,
    facebook: str | None = None,
    phone: str | None = None,
    expected: ExpectedPlace | str | dict | None = None,
    expected_city: str | None = None,
    expected_state: str | None = None,
    expected_country: str | None = None,
    physical_store: str | None = None,
    relationship: str | None = None,
) -> dict[str, Any]:
    """Score one business and choose LEAD, REVIEW, or EXCLUDED."""
    blob = " ".join(
        part
        for part in (business_name, address, city, text, instagram, facebook)
        if part
    )
    place = expected if isinstance(expected, ExpectedPlace) else parse_expected_place(
        expected if isinstance(expected, (str, dict)) else None,
        city=expected_city,
        state=expected_state,
        country=expected_country,
    )
    profile = " ".join(
        part for part in (business_name, city, address, instagram, facebook, text) if part
    )
    geo, geo_evidence = verify_geography(
        profile,
        expected=place,
        website=website,
        address=address,
        city=city,
    )
    physical_score, physical_evidence = _score(blob, PHYSICAL_SIGNALS)
    if address and address.strip() and address.upper() != "UNKNOWN":
        physical_score += 5
        if "explicit physical address" not in physical_evidence:
            physical_evidence.insert(0, "explicit physical address")
    if phone:
        physical_score += 1
        if "phone number" not in physical_evidence:
            physical_evidence.append("phone number")
    if str(physical_store or "").upper() == "YES" and "structured physical store" not in physical_evidence:
        physical_score += 4
        physical_evidence.append("structured physical store")
    online_only = "online-only" in _exclusions(blob)
    physical = _tri(physical_score >= 4 and not online_only, online_only)

    fashion_score, fashion_evidence = _score(blob, FASHION_SIGNALS)
    if fashion_score >= 4:
        fashion = "HIGH"
    elif fashion_score >= 2:
        fashion = "MEDIUM"
    elif fashion_score > 0:
        fashion = "LOW"
    else:
        fashion = "UNKNOWN"

    multi_score, multi_evidence = _score(blob, MULTI_BRAND_SIGNALS)
    concept_score, concept_evidence = _score(blob, CONCEPT_SIGNALS)
    exclusions = _exclusions(blob)
    own_brand = "own-brand-only" in exclusions
    if own_brand:
        multi = "NO"
    elif multi_score >= 4 or concept_score >= 3:
        multi = "YES"
    else:
        multi = "UNKNOWN"

    fit_score, fit_evidence = _score(blob, FIT_SIGNALS)
    if any(item in exclusions for item in ("unrelated", "mass-market", "wholesale-only")):
        fit = "LOW"
    else:
        fit = _band_fit(fit_score)

    retail_model = _retail_model(blob, multi_score, concept_score)
    named = bool(business_name) and business_name.strip().upper() not in {"", "UNKNOWN"}
    contactable = any(value for value in (website, instagram, facebook, phone))
    if relationship in {"ARTICLE", "MEDIA", "DIRECTORY", "SOCIAL_POST"} and not named:
        business = "NO"
    elif named and (contactable or physical == "YES" or website):
        business = "YES"
    elif named:
        business = "YES"
    else:
        business = "UNKNOWN"

    curated = multi == "YES" or concept_score >= 3
    hard_exclusion = [
        item
        for item in exclusions
        if item
        in {
            "online-only",
            "wholesale-only",
            "own-brand-only",
            "bridal-only",
            "saree-only",
            "tailoring-only",
            "accessories-only",
            "mass-market",
            "unrelated",
        }
    ]
    if relationship in {"ARTICLE", "MEDIA", "DIRECTORY"} and not named:
        status = "EXCLUDED"
        reason = "The page is a discovery source, not a retail business."
    elif geo == "NO":
        status = "EXCLUDED"
        reason = "The business location does not match the expected city."
    elif hard_exclusion:
        status = "EXCLUDED"
        reason = "Positive evidence that the business is unsuitable: " + ", ".join(hard_exclusion) + "."
    elif business == "NO":
        status = "EXCLUDED"
        reason = "This record is not a retail business."
    elif (
        physical == "YES"
        and fashion == "HIGH"
        and fit == "HIGH"
        and curated
        and geo != "NO"
    ):
        status = "LEAD"
        reason = (
            "Physical fashion retailer with strong Anazvara fit and "
            "curated or multi-brand evidence."
        )
    elif physical == "YES" and fashion in {"HIGH", "MEDIUM"} and fit in {"HIGH", "MEDIUM"}:
        status = "REVIEW"
        if multi == "UNKNOWN":
            reason = (
                "Strong physical fashion retailer and strong brand fit, "
                "but multi-brand status could not be conclusively verified."
            )
        else:
            reason = "Promising physical fashion retailer. One or more facts still need a manual check."
    elif named and fashion != "NO" and not hard_exclusion:
        status = "REVIEW"
        reason = "Named business with incomplete evidence. Worth a manual look before outreach."
    else:
        status = "REVIEW" if named else "EXCLUDED"
        reason = (
            "Not enough evidence to treat this as a stockist."
            if status == "EXCLUDED"
            else "Named business, but fashion relevance is still unclear."
        )

    geography_confidence = "HIGH" if geo == "YES" and address_confirmed(geo_evidence) else (
        "MEDIUM" if geo == "YES" else ("LOW" if geo == "NO" else "UNKNOWN")
    )
    explanation = format_explanation(
        business_name=business_name or "Unknown",
        physical=physical,
        physical_evidence=physical_evidence,
        fashion=fashion,
        fashion_evidence=fashion_evidence,
        multi=multi,
        multi_evidence=multi_evidence + concept_evidence,
        fit=fit,
        fit_evidence=fit_evidence,
        geo=geo,
        geo_evidence=geo_evidence,
        status=status,
        reason=reason,
    )
    return {
        "physical_store": physical,
        "physical_store_score": physical_score,
        "physical_store_confidence": geography_band(physical_score, physical),
        "evidence_physical_store": physical_evidence,
        "fashion_relevance": fashion,
        "fashion_relevance_score": fashion_score,
        "fashion_relevance_confidence": fashion,
        "evidence_fashion": fashion_evidence,
        "multi_brand": multi,
        "multi_brand_score": multi_score,
        "multi_brand_confidence": "HIGH" if multi == "YES" else ("LOW" if multi == "NO" else "UNKNOWN"),
        "concept_store_score": concept_score,
        "evidence_multi_brand": list(dict.fromkeys(multi_evidence + concept_evidence)),
        "retail_model": retail_model,
        "anazvara_fit": fit,
        "anazvara_fit_score": fit_score,
        "evidence_fit": fit_evidence,
        "geography": geo,
        "geography_confidence": geography_confidence,
        "geographic_evidence": geo_evidence,
        "business_confidence": business,
        "exclusions": hard_exclusion,
        "status": status,
        "reason": reason,
        "explanation": explanation,
    }


def address_confirmed(evidence: list[str]) -> bool:
    return any("address" in item for item in evidence)


def geography_band(score: int, label: str) -> str:
    if label == "NO":
        return "LOW"
    if label == "UNKNOWN":
        return "UNKNOWN"
    if score >= 7:
        return "HIGH"
    if score >= 4:
        return "MEDIUM"
    return "LOW"


def format_explanation(
    *,
    business_name: str,
    physical: str,
    physical_evidence: list[str],
    fashion: str,
    fashion_evidence: list[str],
    multi: str,
    multi_evidence: list[str],
    fit: str,
    fit_evidence: list[str],
    geo: str,
    geo_evidence: list[str],
    status: str,
    reason: str,
) -> str:
    geo_label = {"YES": "VERIFIED", "NO": "MISMATCH", "UNKNOWN": "UNCONFIRMED"}.get(geo, geo)
    return "\n".join(
        [
            f"Business: {business_name}",
            "",
            f"Physical store: {physical}",
            "Evidence: " + (", ".join(physical_evidence) if physical_evidence else "none found"),
            "",
            f"Fashion relevance: {fashion}",
            "Evidence: " + (", ".join(fashion_evidence) if fashion_evidence else "none found"),
            "",
            f"Multi-brand: {multi}",
            "Evidence: " + (", ".join(multi_evidence) if multi_evidence else "none found"),
            "",
            f"Anazvara fit: {fit}",
            "Evidence: " + (", ".join(fit_evidence) if fit_evidence else "none found"),
            "",
            f"Geography: {geo_label}",
            "Evidence: " + (", ".join(geo_evidence) if geo_evidence else "none found"),
            "",
            f"FINAL: {status}",
            "",
            "Reason:",
            reason,
        ]
    )
