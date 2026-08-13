"""Increment 4C — syndicated copies are not independent corroboration."""

import pytest

from bym.knowledge.clustering import (
    assign_clusters,
    corroboration,
    same_story,
    title_similarity,
)
from bym.knowledge.models import EvidenceDocument, canonical_url


def doc(url, title, published="2026-08-15T11:00:00+00:00", byline="", authority="OTHER_MEDIA"):
    return EvidenceDocument(
        url=url,
        title=title,
        acquisition_source="tavily",
        origin_domain=url.split("/")[2],
        origin_authority=authority,
        retrieved_at="2026-08-15T12:00:00+00:00",
        content_hash="h",
        published_at=published,
        canonical=canonical_url(url),
        byline=byline,
        snippet=byline,
    )


def test_canonical_url_strips_tracking():
    assert canonical_url("https://www.bbc.co.uk/sport/123?utm_source=x#top") == (
        "https://bbc.co.uk/sport/123"
    )


def test_same_url_different_tracking_is_one_story():
    a = doc("https://bbc.co.uk/sport/123", "Palmer doubtful")
    b = doc("https://www.bbc.co.uk/sport/123?utm_campaign=y", "Palmer doubtful")
    assert same_story(a, b)


def test_five_syndicated_copies_are_one_evidence_family():
    """The headline case: five sites republishing one PA story must not
    become five independent sources."""
    title = "Chelsea handed Cole Palmer injury boost ahead of Newcastle clash"
    docs = [
        doc(f"https://site{i}.com/football/story-{i}", title, byline="PA Media")
        for i in range(5)
    ]
    report = corroboration(docs)
    assert report.documents == 5
    assert report.evidence_families == 1
    assert not report.independent
    assert "syndicated copies" in report.describe()


def test_genuinely_different_reports_stay_independent():
    docs = [
        doc("https://bbc.co.uk/a", "Palmer assessed ahead of Newcastle trip"),
        doc("https://theathletic.com/b",
            "Maresca confirms three absentees for weekend fixture"),
        doc("https://chelseafc.com/c", "Team news: squad update", authority="OFFICIAL"),
    ]
    report = corroboration(docs)
    assert report.evidence_families == 3
    assert report.independent


def test_similar_headlines_far_apart_in_time_are_separate_stories():
    title = "Chelsea injury update ahead of Newcastle"
    a = doc("https://site1.com/x", title, published="2026-08-01T10:00:00+00:00")
    b = doc("https://site2.com/y", title, published="2026-08-15T10:00:00+00:00")
    assert not same_story(a, b)


def test_title_similarity_bounds():
    assert title_similarity("Palmer injury doubt", "Palmer injury doubt") == 1.0
    assert title_similarity("Palmer injury doubt", "Arsenal sign new keeper") < 0.2
    assert title_similarity("", "anything") == 0.0


def test_clusters_are_stable_and_assigned_to_every_document():
    docs = [
        doc("https://a.com/1", "Same story headline here", byline="Reuters"),
        doc("https://b.com/2", "Same story headline here", byline="Reuters"),
        doc("https://c.com/3", "Completely unrelated transfer news"),
    ]
    assign_clusters(docs)
    assert all(d.story_cluster_id for d in docs)
    assert docs[0].story_cluster_id == docs[1].story_cluster_id
    assert docs[2].story_cluster_id != docs[0].story_cluster_id


def test_empty_input():
    report = corroboration([])
    assert report.documents == 0 and report.evidence_families == 0
