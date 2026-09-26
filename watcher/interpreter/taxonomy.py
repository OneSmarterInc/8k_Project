"""
Guide 4.1: the category list, versioned in the repo.

Five categories, deliberately. With 500 labels and a 70/30 split the
test set is ~150 filings, i.e. ~30 per category. Twelve categories would
leave ~12 each, where an 80% measurement carries a +/-20 point interval.

FREEZE RULE
    Once the test period starts, any edit here must bump TAXONOMY_VERSION
    and force a full re-run. A change made after drift results are visible
    is a route around the firewall (guide 6.5).

DRAFT STATUS
    Definitions and excludes below are a starting draft to edit, not a
    finished taxonomy. "examples" are filled with real accession numbers
    after the first labelling round.
"""

TAXONOMY_VERSION = "1.0.0"

# Material vs routine is answered first. A routine filing has no
# category (blank). Only material filings get one of the five below.
MATERIAL_DEFINITION = (
    "Material: the filing reports an event a reasonable investor would "
    "want to know about - a new or changed agreement, financing, deal, "
    "legal or regulatory development, or other substantive event. "
    "Routine: boilerplate with no new substantive event - e.g. a Reg FD "
    "cover note for an investor deck, shareholder-vote tallies for "
    "uncontested routine proposals, an exhibit-only filing."
)

CATEGORIES = {
    "FINANCING": {
        "label": "Financing",
        "definition": (
            "The company raises, restructures, extends or repays capital."
        ),
        "includes": [
            "term loan or revolver entered, amended or extended",
            "notes or bonds issued or redeemed",
            "equity offering, ATM programme, private placement",
            "convertible notes, warrant issuance or repricing",
        ],
        "excludes": [
            "ordinary trade credit with a supplier (COMMERCIAL)",
            "routine equipment or office lease (not material)",
            "financing that exists only to fund a named acquisition "
            "(MA_STRATEGIC)",
        ],
        "examples": [],
    },
    "COMMERCIAL": {
        "label": "Commercial",
        "definition": (
            "An agreement with a customer, supplier, distributor, licensor "
            "or partner about the company's products or services."
        ),
        "includes": [
            "customer or supply contract",
            "distribution or reseller agreement",
            "licensing or collaboration agreement",
            "termination of any of the above",
        ],
        "excludes": [
            "joint venture or equity stake in a partner (MA_STRATEGIC)",
            "employment agreements with officers (not material unless "
            "part of a departure - then OTHER_MATERIAL)",
        ],
        "examples": [],
    },
    "MA_STRATEGIC": {
        "label": "M&A / Strategic",
        "definition": (
            "A change in what the company owns or who owns it."
        ),
        "includes": [
            "acquisition or merger agreement, signed or completed",
            "divestiture or asset sale",
            "joint venture, strategic investment, letter of intent",
            "financing committed specifically for a named deal",
        ],
        "excludes": [
            "general-purpose financing (FINANCING)",
            "a commercial partnership with no equity or asset transfer "
            "(COMMERCIAL)",
        ],
        "examples": [],
    },
    "LEGAL_REGULATORY": {
        "label": "Legal / Regulatory",
        "definition": (
            "A legal or regulatory event affecting the company."
        ),
        "includes": [
            "litigation filed, ruled on or settled",
            "regulatory action, investigation, consent order",
            "product approval or rejection (e.g. FDA)",
            "delisting or listing-standard notice",
        ],
        "excludes": [
            "a settlement paid by issuing debt - still LEGAL_REGULATORY; "
            "the event is the settlement, not the financing",
        ],
        "examples": [],
    },
    "OTHER_MATERIAL": {
        "label": "Other material",
        "definition": (
            "Genuinely material, but none of the four above. The escape "
            "hatch: use it rather than forcing a guess."
        ),
        "includes": [
            "executive or director departure or appointment",
            "restatement / non-reliance on prior financials",
            "auditor change",
            "restructuring, layoffs, impairment",
        ],
        "excludes": [
            "anything that fits one of the four categories above",
        ],
        "examples": [],
    },
}

# Track this after labelling: consistently above 15% means a category
# is missing and should be split out (guide 4.1).
OTHER_CATEGORY = "OTHER_MATERIAL"


def category_codes():
    return list(CATEGORIES.keys())


def is_valid_category(code):
    return code in CATEGORIES


def taxonomy_payload():
    """Serializable form for the API and, later, the prompt builder."""
    return {
        "version": TAXONOMY_VERSION,
        "material_definition": MATERIAL_DEFINITION,
        "categories": [
            {
                "code": code,
                "label": spec["label"],
                "definition": spec["definition"],
                "includes": list(spec["includes"]),
                "excludes": list(spec["excludes"]),
            }
            for code, spec in CATEGORIES.items()
        ],
    }
