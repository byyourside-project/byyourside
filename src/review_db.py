"""SQLite index of saved rehearsal reviews.

Files in recordings/reviews/<id>/ (audio.wav, raw.json, options.json, report.json)
remain the source of truth. This database only indexes them so past rehearsals can
be listed, opened and deleted, and so segment-level data can be queried later.
"""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS review (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    duration          REAL NOT NULL,
    script            TEXT NOT NULL DEFAULT '',
    target_seconds    REAL,
    speech_span       REAL,
    avg_rate          REAL,
    segment_count     INTEGER NOT NULL DEFAULT 0,
    schedule_status   TEXT,
    review_state      TEXT NOT NULL,
    training_eligible INTEGER NOT NULL DEFAULT 0,
    engine            TEXT NOT NULL,
    schema_version    INTEGER NOT NULL,
    comparison_json   TEXT NOT NULL DEFAULT '[]',
    warnings_json     TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_review_created ON review(created_at);

CREATE TABLE IF NOT EXISTS segment (
    review_id    TEXT NOT NULL REFERENCES review(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    start_sec    REAL NOT NULL,
    end_sec      REAL NOT NULL,
    asr_text     TEXT NOT NULL,
    text         TEXT NOT NULL,
    edited       INTEGER NOT NULL DEFAULT 0,
    hangul_count INTEGER,
    rate         REAL,
    pause_before REAL,
    notes_json   TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (review_id, seq)
);
"""

LIST_COLUMNS = ('id', 'title', 'created_at', 'updated_at', 'duration', 'speech_span', 'avg_rate',
                'segment_count', 'target_seconds', 'schedule_status', 'review_state')


def now():
    return datetime.now().isoformat(timespec='seconds')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class ReviewDB:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        # One short-lived connection per operation keeps this safe under ThreadingHTTPServer.
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def upsert(self, report, created_at=None):
        """Insert or update one review and replace its segments. created_at is kept on update."""
        schedule = report.get('schedule') or {}
        summary = report.get('summary') or {}
        stamp = now()
        row = {
            'id': report['id'],
            'title': report.get('title') or '연습 발표',
            'created_at': created_at or stamp,
            'updated_at': stamp,
            'duration': report.get('duration') or 0,
            'script': report.get('script') or '',
            'target_seconds': schedule.get('target_seconds'),
            'speech_span': summary.get('speech_span_seconds'),
            'avg_rate': summary.get('rate'),
            'segment_count': summary.get('segment_count', len(report.get('segments', []))),
            'schedule_status': schedule.get('status'),
            'review_state': report.get('review_state', 'automatic_unverified'),
            'training_eligible': int(bool(report.get('training_eligible'))),
            'engine': report.get('engine', ''),
            'schema_version': report.get('schema_version', 1),
            'comparison_json': dumps(report.get('comparison', [])),
            'warnings_json': dumps(report.get('warnings', [])),
        }
        columns = ', '.join(row)
        placeholders = ', '.join(f':{key}' for key in row)
        updates = ', '.join(f'{key} = excluded.{key}' for key in row if key not in ('id', 'created_at'))
        segments = [{
            'review_id': row['id'], 'seq': s['id'], 'start_sec': s['start'], 'end_sec': s['end'],
            'asr_text': s.get('asr_text', s['text']), 'text': s['text'], 'edited': int(bool(s.get('edited'))),
            'hangul_count': s.get('hangul_count'), 'rate': s.get('rate'), 'pause_before': s.get('pause_before'),
            'notes_json': dumps(s.get('notes', [])),
        } for s in report.get('segments', [])]
        with self.transaction() as conn:
            conn.execute(f'INSERT INTO review ({columns}) VALUES ({placeholders}) '
                         f'ON CONFLICT(id) DO UPDATE SET {updates}', row)
            conn.execute('DELETE FROM segment WHERE review_id = ?', (row['id'],))
            conn.executemany(
                'INSERT INTO segment (review_id, seq, start_sec, end_sec, asr_text, text, edited, '
                'hangul_count, rate, pause_before, notes_json) VALUES (:review_id, :seq, :start_sec, '
                ':end_sec, :asr_text, :text, :edited, :hangul_count, :rate, :pause_before, :notes_json)',
                segments)

    def list_reviews(self):
        with self.transaction() as conn:
            rows = conn.execute(f'SELECT {", ".join(LIST_COLUMNS)} FROM review '
                                'ORDER BY created_at DESC, id').fetchall()
        return [dict(row) for row in rows]

    def segments(self, review_id):
        with self.transaction() as conn:
            rows = conn.execute('SELECT * FROM segment WHERE review_id = ? ORDER BY seq', (review_id,)).fetchall()
        return [dict(row) for row in rows]

    def exists(self, review_id):
        with self.transaction() as conn:
            return conn.execute('SELECT 1 FROM review WHERE id = ?', (review_id,)).fetchone() is not None

    def delete(self, review_id):
        with self.transaction() as conn:
            conn.execute('DELETE FROM review WHERE id = ?', (review_id,))

    def sync(self, storage):
        """Index report.json files that are not in the DB yet and drop rows whose folder is gone."""
        storage = Path(storage)
        with self.transaction() as conn:
            known = {row[0] for row in conn.execute('SELECT id FROM review')}
        present = set()
        for folder in storage.iterdir() if storage.is_dir() else ():
            report_path = folder / 'report.json'
            if not re.fullmatch(r'[a-f0-9]{32}', folder.name) or not report_path.is_file():
                continue
            present.add(folder.name)
            if folder.name in known:
                continue
            try:
                report = json.loads(report_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue  # A broken report stays on disk but is not listed.
            report['id'] = folder.name
            created = datetime.fromtimestamp(report_path.stat().st_mtime).isoformat(timespec='seconds')
            self.upsert(report, created_at=created)
        for stale in known - present:
            self.delete(stale)
