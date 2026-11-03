import logging

from social_helpers import drain, start, targets
from test_social_copy import COPY

from app.social.adapters.base import ProviderError
from app.social.adapters.x import XPublisher
from app.social.config import SocialSettings


def worker_logs(caplog):
    return [r for r in caplog.records if r.name == "app.social.worker"]


def test_each_step_and_the_outcome_are_logged_with_duration_and_cost(
    client, headers, render, publishing, clock, caplog
):
    worker, adapters, _calls = publishing
    adapters["x"].cost_per_post_usd = 0.25
    caplog.set_level(logging.INFO, logger="app.social.worker")
    assert (
        start(client, headers, render, targets(client, headers, ["x", "youtube"])).status_code
        == 202
    )
    drain(worker, clock)
    lines = [r.getMessage() for r in worker_logs(caplog)]
    steps = [line for line in lines if " step=" in line]
    assert any("platform=x step=publish status=processing attempt=1" in line for line in steps)
    assert all(" took=" in line for line in steps)
    done = {
        line.split("platform=")[1].split()[0]: line for line in lines if "result=published" in line
    }
    assert set(done) == {"x", "youtube"}
    assert "api_cost_usd=0.2500" in done["x"] and "api_cost_usd=0.0000" in done["youtube"]
    assert all("attempts=1" in line and " total=" in line for line in done.values())


def test_failures_log_the_error_code(client, headers, render, publishing, clock, caplog):
    worker, adapters, _calls = publishing
    adapters["youtube"].failures = [ProviderError("MEDIA_REJECTED")]
    caplog.set_level(logging.INFO, logger="app.social.worker")
    start(client, headers, render, targets(client, headers, ["youtube"]))
    drain(worker, clock)
    failed = [r for r in worker_logs(caplog) if "result=failed" in r.getMessage()]
    assert len(failed) == 1 and failed[0].levelno == logging.WARNING
    assert (
        "platform=youtube" in failed[0].getMessage()
        and "error=MEDIA_REJECTED" in failed[0].getMessage()
    )


def test_logs_never_contain_tokens_captions_or_video_links(
    client, headers, render, publishing, clock, caplog
):
    worker, adapters, _calls = publishing
    caplog.set_level(logging.DEBUG)
    start(client, headers, render, targets(client, headers, ["instagram", "tiktok", "x"]))
    drain(worker, clock)
    text = caplog.text
    secrets = [
        "private-access",
        "private-refresh",
        "rotated-access",
        *adapters["instagram"].media_urls,
    ]
    captions = [COPY["instagram"]["caption"], COPY["tiktok"]["caption"], COPY["x"]["text"]]
    assert adapters["instagram"].media_urls and all(s not in text for s in secrets + captions)
    assert "signature=" not in text and "access_token" not in text


def test_x_cost_per_post_comes_from_configuration(monkeypatch):
    monkeypatch.setenv("X_COST_PER_POST", "0.15")
    assert XPublisher(SocialSettings(), None).cost_per_post_usd == 0.15
    monkeypatch.delenv("X_COST_PER_POST")
    assert XPublisher(SocialSettings(), None).cost_per_post_usd == 0.0
