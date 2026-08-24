"""The learned classifier: accuracy, incrementality, and cold-start behaviour."""

from __future__ import annotations

from timesplit.core.features import extract_features
from timesplit.core.models import Activity
from timesplit.core.naive_bayes import NaiveBayes, correction_weight, rebuild

SCHOOL, REAL_ESTATE = 1, 2

# What the model actually learns from: the windows you corrected. Real usage
# repeats heavily -- the same handful of sites and documents, over and over --
# so the training set reflects that rather than 30 unique one-offs.
TRAINING: list[tuple[Activity, int]] = [
    (Activity("chrome.exe", "Canvas Gradebook - BIO 101", "https://canvas.myschool.edu/courses/1/gradebook"), SCHOOL),
    (Activity("chrome.exe", "Attendance - Powerschool", "https://ps.myschool.edu/attendance"), SCHOOL),
    (Activity("chrome.exe", "Course modules", "https://canvas.myschool.edu/courses/2/modules"), SCHOOL),
    (Activity("chrome.exe", "Grade submission", "https://ps.myschool.edu/grades"), SCHOOL),
    (Activity("word.exe", "Lesson plan week 4.docx"), SCHOOL),
    (Activity("word.exe", "Syllabus fall semester.docx"), SCHOOL),
    (Activity("word.exe", "Report card comments.docx"), SCHOOL),
    (Activity("excel.exe", "Student roster.xlsx"), SCHOOL),
    (Activity("excel.exe", "Grade calculations.xlsx"), SCHOOL),
    (Activity("outlook.exe", "Faculty meeting Thursday - Inbox"), SCHOOL),
    (Activity("powerpnt.exe", "Photosynthesis unit.pptx"), SCHOOL),
    (Activity("zoom.exe", "Parent conference - Zoom Meeting"), SCHOOL),

    (Activity("chrome.exe", "12 Oak St listing", "https://zillow.com/homedetails/12-oak"), REAL_ESTATE),
    (Activity("chrome.exe", "44 Elm listing", "https://zillow.com/homedetails/44-elm"), REAL_ESTATE),
    (Activity("chrome.exe", "MLS search results", "https://matrix.mlsmatrix.com/Matrix/Search"), REAL_ESTATE),
    (Activity("chrome.exe", "Purchase agreement - dotloop", "https://dotloop.com/my/loops/9"), REAL_ESTATE),
    (Activity("chrome.exe", "Loop documents - dotloop", "https://dotloop.com/my/loops/12"), REAL_ESTATE),
    (Activity("word.exe", "Listing agreement 44 Elm.docx"), REAL_ESTATE),
    (Activity("word.exe", "Seller disclosure 12 Oak.docx"), REAL_ESTATE),
    (Activity("word.exe", "Buyer representation agreement.docx"), REAL_ESTATE),
    (Activity("excel.exe", "Commission tracker.xlsx"), REAL_ESTATE),
    (Activity("excel.exe", "Escrow ledger.xlsx"), REAL_ESTATE),
    (Activity("outlook.exe", "Escrow instructions - Inbox"), REAL_ESTATE),
    (Activity("comet.exe", "Redfin - 88 Pine", "https://redfin.com/home/88-pine"), REAL_ESTATE),
]

# Windows the model has never seen, but which share vocabulary with training --
# the same sites and documents, different pages. This is the case the model
# exists to handle.
HOLDOUT: list[tuple[Activity, int]] = [
    (Activity("chrome.exe", "Canvas Gradebook - CHEM 202", "https://canvas.myschool.edu/courses/7/gradebook"), SCHOOL),
    (Activity("chrome.exe", "Attendance report", "https://ps.myschool.edu/attendance/week"), SCHOOL),
    (Activity("word.exe", "Lesson plan week 9.docx"), SCHOOL),
    (Activity("excel.exe", "Student roster period 3.xlsx"), SCHOOL),
    (Activity("chrome.exe", "Course announcements", "https://canvas.myschool.edu/courses/3/announcements"), SCHOOL),
    (Activity("chrome.exe", "88 Pine listing", "https://zillow.com/homedetails/88-pine"), REAL_ESTATE),
    (Activity("chrome.exe", "MLS map search", "https://matrix.mlsmatrix.com/Matrix/Map"), REAL_ESTATE),
    (Activity("word.exe", "Listing agreement 88 Pine.docx"), REAL_ESTATE),
    (Activity("chrome.exe", "Purchase agreement signed - dotloop", "https://dotloop.com/my/loops/31"), REAL_ESTATE),
    (Activity("excel.exe", "Commission tracker 2026.xlsx"), REAL_ESTATE),
]

CORPUS = TRAINING + HOLDOUT


def train(model: NaiveBayes, items) -> None:
    for activity, label in items:
        model.learn(extract_features(activity), label)


def test_accuracy_on_unseen_pages_of_familiar_sites():
    """The job the model actually has: new pages on sites you have corrected."""
    model = NaiveBayes()
    train(model, TRAINING)

    correct = sum(
        1
        for activity, label in HOLDOUT
        if model.predict(extract_features(activity), [SCHOOL, REAL_ESTATE]).category_id == label
    )
    accuracy = correct / len(HOLDOUT)
    assert accuracy >= 0.9, f"holdout accuracy {accuracy:.0%} on {len(HOLDOUT)} items"


def test_unfamiliar_windows_are_not_answered_confidently():
    """Nothing recognisable must land in the review queue, not be guessed at.

    Overconfidence here is the failure that makes a review queue useless, so
    it gets its own test.
    """
    model = NaiveBayes()
    train(model, TRAINING)
    strangers = [
        Activity("spotify.exe", "Discover Weekly", "https://open.spotify.com/playlist/1"),
        Activity("steam.exe", "Library"),
        Activity("chrome.exe", "Flight status", "https://united.com/flights/992"),
    ]
    for activity in strangers:
        prediction = model.predict(extract_features(activity), [SCHOOL, REAL_ESTATE])
        assert prediction.confidence < 0.75, (
            f"{activity.title!r} answered at {prediction.confidence:.0%} confidence"
        )


def test_incremental_learning_equals_a_full_rebuild():
    """The repair path must reproduce the live model exactly."""
    model = NaiveBayes()
    corrections = []
    for activity, label in CORPUS:
        feats = extract_features(activity)
        model.learn(feats, label)
        corrections.append({"features": feats, "new_category_id": label,
                            "old_category_id": None, "weight": 1.0})

    rebuilt = rebuild(corrections)
    assert rebuilt.classes == model.classes
    assert rebuilt.tokens == model.tokens


def test_rebuild_reproduces_overturned_labels():
    model = NaiveBayes()
    feats = extract_features(CORPUS[0][0])
    model.learn(feats, SCHOOL)
    model.unlearn(feats, SCHOOL)
    model.learn(feats, REAL_ESTATE)

    rebuilt = rebuild([
        {"features": feats, "new_category_id": SCHOOL, "old_category_id": None, "weight": 1.0},
        {"features": feats, "new_category_id": REAL_ESTATE, "old_category_id": SCHOOL, "weight": 1.0},
    ])
    assert rebuilt.tokens == model.tokens
    assert rebuilt.classes == model.classes


def test_unlearn_never_goes_negative():
    model = NaiveBayes()
    feats = ["exe:chrome.exe", "t:escrow"]
    model.learn(feats, SCHOOL, weight=1.0)
    model.unlearn(feats, SCHOOL, weight=99.0)
    assert all(v >= 0 for v in model.tokens.get(SCHOOL, {}).values())
    docs, total = model.classes[SCHOOL]
    assert docs >= 0 and total >= 0


def test_model_stays_silent_until_it_has_evidence():
    """A confident wrong answer in week one is worse than an honest 'unknown'."""
    model = NaiveBayes()
    train(model, CORPUS[:5])
    assert not model.is_warm(min_docs=20)
    train(model, CORPUS[5:])
    assert model.is_warm(min_docs=20)


def test_predictions_are_confined_to_real_jobs():
    model = NaiveBayes()
    train(model, CORPUS)
    prediction = model.predict(extract_features(CORPUS[0][0]), allowed=[SCHOOL, REAL_ESTATE])
    assert set(prediction.posteriors) <= {SCHOOL, REAL_ESTATE}
    assert abs(sum(prediction.posteriors.values()) - 1.0) < 1e-9


def test_confidence_is_low_for_something_never_seen():
    model = NaiveBayes()
    train(model, CORPUS)
    unknown = Activity("spotify.exe", "Discover Weekly", "https://open.spotify.com/playlist/1")
    prediction = model.predict(extract_features(unknown), [SCHOOL, REAL_ESTATE])
    assert prediction.confidence < 0.95


def test_long_sessions_teach_more_than_brief_ones():
    assert correction_weight(30) < correction_weight(1800) < correction_weight(7200)
    assert correction_weight(999999) <= 3.0


def test_evidence_names_the_deciding_features():
    model = NaiveBayes()
    train(model, CORPUS)
    prediction = model.predict(
        extract_features(Activity("chrome.exe", "escrow", "https://dotloop.com/my/loops/1")),
        [SCHOOL, REAL_ESTATE],
    )
    assert prediction.category_id == REAL_ESTATE
    assert prediction.evidence, "the dashboard needs something to show"
