"""Processing worker for real transcription and analysis: run with `make worker`.

Jobs are claimed with a lease so several workers (or a restarted one) never run the
same stage twice. Slow work (Whisper) runs outside database transactions; the
worker renews its lease while decoding and abandons the stage when the job is
canceled or deleted meanwhile.
"""

import logging
import signal
import time

from sqlalchemy import or_, select

from ..config import Settings
from ..database import Database
from ..db_models import JobRecord, ProjectRecord, TranscriptRecord, UploadRecord
from ..store import Store, oid
from ..transcription import (
    TranscriptionError,
    build_transcriber,
    load_or_transcribe,
    source_digest,
    to_segments,
)

logger = logging.getLogger(__name__)


class Abandoned(Exception):
    """The job stopped belonging to this worker (canceled, deleted or lease lost)."""


class ProcessingWorker:
    def __init__(self, database, settings, transcriber=None, clock=time.time):
        self.database, self.settings, self.clock = database, settings, clock
        self._transcriber = transcriber
        self.owner = oid("pworker")[:40]
        self.heartbeat_seconds = 15

    @property
    def transcriber(self):
        if self._transcriber is None:
            self._transcriber = build_transcriber(self.settings)
        return self._transcriber

    def now(self):
        return int(self.clock() * 1000)

    def store(self, db):
        return Store(db, self.settings, self.clock)

    def claim(self):
        now = self.now()
        with self.database.transaction() as db:
            job = db.scalar(
                select(JobRecord)
                .where(
                    JobRecord.state == "running",
                    or_(JobRecord.leaseUntil.is_(None), JobRecord.leaseUntil <= now),
                )
                .order_by(JobRecord.queuedAt, JobRecord.id)
                .limit(1)
            )
            if job is None:
                return None
            job.leaseOwner = self.owner
            job.leaseUntil = now + self.settings.job_lease_seconds * 1000
            return job.id, job.stages[job.index]

    def run_once(self) -> bool:
        claimed = self.claim()
        if not claimed:
            return False
        job_id, stage = claimed
        try:
            if stage == "transcribing":
                self.transcribe(job_id)
            else:
                self.analyze(job_id)
        except Abandoned:
            logger.info("Job %s was canceled or reassigned; result discarded", job_id)
        return True

    def owned_job(self, db, job_id, stage) -> JobRecord:
        job = db.get(JobRecord, job_id)
        if (
            job is None
            or job.state != "running"
            or job.leaseOwner != self.owner
            or job.stages[job.index] != stage
        ):
            raise Abandoned
        return job

    def fail(self, job_id, stage, code):
        try:
            with self.database.transaction() as db:
                job = self.owned_job(db, job_id, stage)
                self.store(db).fail_stage(
                    job, db.get(ProjectRecord, job.projectId), code, self.now()
                )
        except Abandoned:
            pass

    def transcribe(self, job_id):
        with self.database.transaction() as db:
            job = self.owned_job(db, job_id, "transcribing")
            upload = db.scalar(select(UploadRecord).where(UploadRecord.projectId == job.projectId))
            root = (self.settings.media_dir / "sources").resolve()
            source = (root / upload.storedName).resolve() if upload and upload.storedName else None
        if source is None or source.parent != root or not source.is_file():
            logger.warning("Job %s has no source file to transcribe", job_id)
            return self.fail(job_id, "transcribing", "TRANSCRIPTION_FAILED")

        last_beat = [0.0]

        def heartbeat(fraction):
            if time.monotonic() - last_beat[0] < self.heartbeat_seconds:
                return
            last_beat[0] = time.monotonic()
            with self.database.transaction() as db:
                job = self.owned_job(db, job_id, "transcribing")
                job.leaseUntil = self.now() + self.settings.job_lease_seconds * 1000
                job.stageProgress = round(fraction, 3)

        try:
            digest = source_digest(source)
            result, digest, cached = load_or_transcribe(
                source, self.settings.media_dir, self.transcriber, heartbeat, digest
            )
        except Abandoned:
            raise
        except (TranscriptionError, OSError):
            logger.exception("Transcription failed for job %s", job_id)
            return self.fail(job_id, "transcribing", "TRANSCRIPTION_FAILED")

        segments = to_segments(result)
        with self.database.transaction() as db:
            job = self.owned_job(db, job_id, "transcribing")
            p = db.get(ProjectRecord, job.projectId)
            store = self.store(db)
            existing = db.get(TranscriptRecord, p.id)
            if existing is not None:
                db.delete(existing)
                db.flush()
            db.add(
                TranscriptRecord(
                    projectId=p.id,
                    language=result["language"] or "en",
                    revision=1,
                    userCorrected=False,
                    segments=segments,
                    sourceDigest=digest,
                )
            )
            p.update(language=result["language"] or "en", transcriptRevision=1)
            store.charge(p, job, p.durationSec)
            if not segments:
                # Silence, music only, or no intelligible speech: no captions or highlights.
                logger.info("No speech found for project %s", p.id)
                store.fail_stage(job, p, "NO_SPEECH", self.now())
            else:
                store.stage_done(job, p, self.now())
        logger.info(
            "Job %s transcribed (%d segments, cached=%s, %.1fs)",
            job_id,
            len(segments),
            cached,
            result.get("transcriptionSeconds", 0),
        )

    def analyze(self, job_id):
        started = time.monotonic()
        try:
            with self.database.transaction() as db:
                job = self.owned_job(db, job_id, "analyzing")
                p = db.get(ProjectRecord, job.projectId)
                store = self.store(db)
                store.analyze(p, job.params)
                store.stage_done(job, p, self.now())
        except Abandoned:
            raise
        except Exception:
            logger.exception("Analysis failed for job %s", job_id)
            return self.fail(job_id, "analyzing", "ANALYSIS_FAILED")
        logger.info("Job %s analyzed in %.1fs", job_id, time.monotonic() - started)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings()
    if settings.processing_mode == "demo":
        logger.info("PROCESSING_MODE=demo: the API simulates processing; worker not needed")
        return
    database = Database(settings.database_url)
    database.initialize()
    worker = ProcessingWorker(database, settings)
    running = True

    def stop(*args):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("Processing worker started (Whisper %s)", settings.whisper_model)
    try:
        while running:
            if not worker.run_once():
                time.sleep(1)
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
