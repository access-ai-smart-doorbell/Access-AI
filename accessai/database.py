"""
Database - persists Visitor Events to SQLite via SQLAlchemy 2.x.

The schema is created in FULL in Phase 1, including tables that only later
phases populate (known_faces, reid_gallery, unknown_face_clusters). Creating
them now keeps the schema stable so later phases never have to migrate.

Only the `events` table is written to in Phase 1.
"""

import json
import datetime as _dt
from typing import List, Optional

from sqlalchemy import (
    create_engine, Integer, Float, String, Text, LargeBinary, DateTime,
    Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Mapped, mapped_column

from .visitor_event import (VisitorEvent, Identity, DetectedObject,
                            people_to_dicts, alert_kind)

Base = declarative_base()


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    timestamp: Mapped[str] = mapped_column(String(32))
    trigger: Mapped[str] = mapped_column(String(16), default="manual")

    # Identity (flattened)
    identity_name: Mapped[str] = mapped_column(String(128), default="Unknown")
    identity_known: Mapped[int] = mapped_column(Integer, default=0)
    identity_conf: Mapped[float] = mapped_column(Float, default=0.0)

    # Anti-spoof
    is_spoof: Mapped[int] = mapped_column(Integer, default=0)
    spoof_score: Mapped[float] = mapped_column(Float, default=1.0)

    # Vision
    visitor_count: Mapped[int] = mapped_column(Integer, default=0)
    carried_objects: Mapped[str] = mapped_column(Text, default="[]")   # JSON list
    scene_summary: Mapped[str] = mapped_column(Text, default="")
    ocr_text: Mapped[str] = mapped_column(Text, default="")

    # Multi-person (Phase 15). `people` is a JSON list of per-person dicts
    # (name/known/age/gender/appearance/expression/is_spoof/box); `extra_unknown`
    # is the count of face-less people YOLO saw beyond the detected faces. Both
    # additive + nullable so an in-place ALTER on an old DB is happy.
    people: Mapped[str] = mapped_column(Text, default="[]")            # JSON list
    extra_unknown: Mapped[int] = mapped_column(Integer, default=0)

    # Visitor description (Phase 12) - approximate age/gender + VLM appearance.
    # nullable so an old DB migrated in-place (ALTER TABLE ADD COLUMN) is happy.
    age: Mapped[Optional[int]] = mapped_column(Integer, default=None, nullable=True)
    gender: Mapped[str] = mapped_column(String(16), default="")
    appearance: Mapped[str] = mapped_column(Text, default="")

    # Speech
    speech_transcript: Mapped[str] = mapped_column(Text, default="")
    language_detected: Mapped[str] = mapped_column(String(16), default="")
    translated_transcript: Mapped[str] = mapped_column(Text, default="")

    # Re-ID
    reid_id: Mapped[Optional[str]] = mapped_column(String(64), default=None, nullable=True)
    reid_seen_count: Mapped[int] = mapped_column(Integer, default=0)

    # Context engine
    intent: Mapped[str] = mapped_column(String(64), default="unknown visitor")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    announcement_text: Mapped[str] = mapped_column(Text, default="")

    # Storage
    snapshot_path: Mapped[str] = mapped_column(Text, default="")


class KnownFaceRow(Base):
    __tablename__ = "known_faces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    source_path: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_dt.datetime.utcnow)


class ReidRow(Base):
    __tablename__ = "reid_gallery"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reid_id: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)
    last_seen: Mapped[_dt.datetime] = mapped_column(DateTime, default=_dt.datetime.utcnow)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)


class UnknownClusterRow(Base):
    __tablename__ = "unknown_face_clusters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)
    event_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_dt.datetime.utcnow)
    suggested: Mapped[int] = mapped_column(Integer, default=0)


class PushTokenRow(Base):
    """A registered push-notification device token (docs/MOBILE_PUSH.md).

    Additive Phase-17 scaffolding: /register_push stores tokens here so a
    doorbell event can later be delivered to a CLOSED app via FCM. `platform`
    is a freeform hint ("web" | "android" | "ios"); `mode` mirrors the
    device's accessibility mode so a future sender can tailor the payload."""
    __tablename__ = "push_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(Text, unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(16), default="web")
    mode: Mapped[str] = mapped_column(String(16), default="both")
    created_at: Mapped[_dt.datetime] = mapped_column(DateTime, default=_dt.datetime.utcnow)
    last_seen: Mapped[_dt.datetime] = mapped_column(DateTime, default=_dt.datetime.utcnow)


Index("ix_events_timestamp", EventRow.timestamp)


class Database:
    def __init__(self, path: str):
        # `future=True` is the SQLAlchemy 2.x style engine.
        self.engine = create_engine(f"sqlite:///{path}", future=True,
                                     connect_args={"check_same_thread": False})
        # Concurrency: writers arrive from server executor threads AND the
        # background VLM-enrich thread. Default SQLite journaling takes an
        # exclusive lock per write, so overlapping writes raise "database is
        # locked". WAL lets readers proceed during a write, and busy_timeout
        # makes a competing writer wait (up to 5s) instead of raising.
        from sqlalchemy import event as _sa_event

        @_sa_event.listens_for(self.engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA synchronous=NORMAL")   # safe with WAL, faster
            cur.close()

        Base.metadata.create_all(self.engine)
        # Phase 12: create_all() only CREATES missing tables - it never ADDs a
        # column to a table that already exists. An older accessai.db therefore
        # lacks the new age/gender/appearance columns, and inserting would fail.
        # SQLite supports cheap in-place `ALTER TABLE ... ADD COLUMN`, so we add
        # any missing columns here. Idempotent + fail-soft: on a fresh DB (columns
        # already present) each ALTER is skipped.
        self._ensure_columns()
        self.Session = sessionmaker(bind=self.engine, future=True)
        print(f"[Database] Ready at {path}")

    def _ensure_columns(self) -> None:
        """Add Phase-12 columns to an existing `events` table if they're missing."""
        from sqlalchemy import text
        wanted = {
            "age": "INTEGER",
            "gender": "VARCHAR(16) DEFAULT ''",
            "appearance": "TEXT DEFAULT ''",
            "people": "TEXT DEFAULT '[]'",                 # Phase 15
            "extra_unknown": "INTEGER DEFAULT 0",          # Phase 15
        }
        try:
            with self.engine.begin() as conn:
                cols = {row[1] for row in
                        conn.exec_driver_sql("PRAGMA table_info(events)").fetchall()}
                for name, ddl in wanted.items():
                    if name not in cols:
                        conn.exec_driver_sql(
                            f"ALTER TABLE events ADD COLUMN {name} {ddl}")
                        print(f"[Database] Migrated: added events.{name}")
        except Exception as e:                            # pragma: no cover
            print(f"[Database] Column migration skipped ({e}).")

    # ------------------------------------------------------------------
    def save_event(self, ev: VisitorEvent) -> None:
        row = EventRow(
            event_id=ev.event_id,
            timestamp=ev.timestamp,
            trigger=ev.trigger,
            identity_name=ev.identity.name,
            identity_known=1 if ev.identity.known else 0,
            identity_conf=float(ev.identity.confidence),
            is_spoof=1 if ev.is_spoof else 0,
            spoof_score=float(ev.spoof_score),
            visitor_count=int(ev.visitor_count),
            carried_objects=json.dumps(ev.carried_objects),
            scene_summary=ev.scene_summary,
            ocr_text=ev.ocr_text,
            people=json.dumps(people_to_dicts(ev.people)),
            extra_unknown=int(ev.extra_unknown),
            age=ev.age,
            gender=ev.gender,
            appearance=ev.appearance,
            speech_transcript=ev.speech_transcript,
            language_detected=ev.language_detected,
            translated_transcript=ev.translated_transcript,
            reid_id=ev.reid_id,
            reid_seen_count=int(ev.reid_seen_count),
            intent=ev.intent,
            confidence=float(ev.confidence),
            announcement_text=ev.announcement_text,
            snapshot_path=ev.snapshot_path,
        )
        with self.Session() as s:
            s.add(row)
            s.commit()

    def recent_events(self, limit: int = 50, q: str = "") -> List[dict]:
        """Newest events first; `q` (optional) filters case-insensitively
        across the human-searchable fields: who (name), why (intent), what
        was said/announced, the date prefix, and how it was triggered.
        Objective 7: 'a searchable visitor history'."""
        with self.Session() as s:
            query = s.query(EventRow)
            q = (q or "").strip()
            if q:
                from sqlalchemy import or_
                like = f"%{q}%"
                query = query.filter(or_(
                    EventRow.identity_name.ilike(like),
                    EventRow.intent.ilike(like),
                    EventRow.announcement_text.ilike(like),
                    EventRow.speech_transcript.ilike(like),
                    EventRow.scene_summary.ilike(like),
                    EventRow.ocr_text.ilike(like),
                    EventRow.people.ilike(like),      # JSON: matches any name
                    EventRow.timestamp.ilike(f"{q}%"),  # date prefix 2026-07-19
                    EventRow.trigger.ilike(like),
                ))
            rows = (
                query
                .order_by(EventRow.id.desc())
                .limit(limit)
                .all()
            )
            return [self._event_row_to_dict(r) for r in rows]

    def get_event(self, event_id: str) -> Optional[dict]:
        with self.Session() as s:
            r = s.query(EventRow).filter(EventRow.event_id == event_id).first()
            return self._event_row_to_dict(r) if r else None

    def latest_event_id(self) -> Optional[str]:
        """The most recently inserted event's id (for /hear_visitor to attach to)."""
        with self.Session() as s:
            r = s.query(EventRow).order_by(EventRow.id.desc()).first()
            return r.event_id if r else None

    # --- Visit-history removal (dashboard "delete" / "clear all") -------------
    # Only EventRow is touched: known people, the re-ID gallery, and unknown
    # clusters are left intact. Each method returns snapshot path(s) so the
    # server can also delete the saved .jpg from disk.
    def delete_event(self, event_id: str) -> Optional[str]:
        """Delete ONE event; return its snapshot_path ("" if none), or None if
        the id is unknown."""
        with self.Session() as s:
            r = s.query(EventRow).filter(EventRow.event_id == event_id).first()
            if r is None:
                return None
            path = r.snapshot_path or ""
            s.delete(r)
            s.commit()
            return path

    def clear_events(self) -> List[str]:
        """Delete ALL events; return their snapshot paths for file cleanup."""
        with self.Session() as s:
            rows = s.query(EventRow).all()
            paths = [r.snapshot_path for r in rows if r.snapshot_path]
            for r in rows:
                s.delete(r)
            s.commit()
            return paths

    # Phase 12: ADDITIVE update path. Both /hear_visitor (attach a transcript to
    # the latest event) and the background VLM enrich (fill appearance/scene/ocr
    # + a refreshed announcement onto the already-saved event) call this. Only a
    # whitelist of columns is writable; unknown keys are ignored. Returns the
    # refreshed event dict (or None if the id is gone). Never raises.
    _UPDATABLE = {
        "scene_summary", "ocr_text", "speech_transcript", "language_detected",
        "translated_transcript", "intent", "confidence", "announcement_text",
        "age", "gender", "appearance", "visitor_count", "people", "extra_unknown",
    }

    def update_event_fields(self, event_id: str, **fields) -> Optional[dict]:
        if not event_id or not fields:
            return None
        try:
            with self.Session() as s:
                r = (s.query(EventRow)
                     .filter(EventRow.event_id == event_id).first())
                if r is None:
                    return None
                for k, v in fields.items():
                    if k in self._UPDATABLE:
                        setattr(r, k, v)
                s.commit()
                return self._event_row_to_dict(r)
        except Exception as e:                            # pragma: no cover
            print(f"[Database] update_event_fields failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Phase 9: Re-ID gallery helpers.
    #
    # One row per reid_id holds a representative appearance embedding plus a
    # rolling seen_count / last_seen. All recency math uses the EVENT's own clock
    # (the ISO timestamp passed in) so it never mixes local vs UTC. Embeddings are
    # stored as raw float32 bytes (np.tobytes) and rebuilt with np.frombuffer.
    # The reid_gallery table already exists (Phase 1); we only add methods here.
    # ------------------------------------------------------------------
    def reid_add(self, reid_id: str, embedding: bytes, now_iso: str = "",
                 seen_count: int = 1) -> None:
        with self.Session() as s:
            s.add(ReidRow(reid_id=reid_id, embedding=embedding,
                          last_seen=_parse_iso(now_iso), seen_count=seen_count))
            s.commit()

    def reid_recent(self, now_iso: str = "", ttl_hours: float = 24.0) -> List[dict]:
        cutoff = _parse_iso(now_iso) - _dt.timedelta(hours=ttl_hours)
        with self.Session() as s:
            rows = (s.query(ReidRow)
                    .filter(ReidRow.last_seen >= cutoff)
                    .order_by(ReidRow.last_seen.desc())
                    .all())
            return [{"reid_id": r.reid_id, "embedding": r.embedding,
                     "last_seen": r.last_seen, "seen_count": r.seen_count}
                    for r in rows]

    def reid_touch(self, reid_id: str, now_iso: str = "") -> int:
        """Bump an existing reid_id's seen_count + last_seen; return new count."""
        with self.Session() as s:
            r = (s.query(ReidRow)
                 .filter(ReidRow.reid_id == reid_id)
                 .order_by(ReidRow.id.desc())
                 .first())
            if r is None:
                return 0
            r.seen_count = int(r.seen_count) + 1
            r.last_seen = _parse_iso(now_iso)
            s.commit()
            return int(r.seen_count)

    def reid_evict(self, now_iso: str = "", ttl_hours: float = 24.0,
                   max_gallery: int = 500) -> None:
        """Drop sightings older than the TTL, then cap to max_gallery (oldest)."""
        cutoff = _parse_iso(now_iso) - _dt.timedelta(hours=ttl_hours)
        with self.Session() as s:
            for r in s.query(ReidRow).filter(ReidRow.last_seen < cutoff).all():
                s.delete(r)
            total = s.query(ReidRow).count()
            if total > max_gallery:
                extra = total - max_gallery
                for r in (s.query(ReidRow)
                          .order_by(ReidRow.last_seen.asc())
                          .limit(extra).all()):
                    s.delete(r)
            s.commit()

    def reid_count(self) -> int:
        with self.Session() as s:
            return s.query(ReidRow).count()

    # ------------------------------------------------------------------
    # Phase 17: push-token registry (docs/MOBILE_PUSH.md scaffolding).
    # Tokens are upserted (a device re-registering refreshes last_seen and
    # mode); the future FCM sender iterates push_tokens() per event.
    # ------------------------------------------------------------------
    def push_register(self, token: str, platform: str = "web",
                      mode: str = "both") -> bool:
        """Store or refresh a device push token. Returns True if NEW."""
        with self.Session() as s:
            row = s.query(PushTokenRow).filter(
                PushTokenRow.token == token).first()
            if row is not None:
                row.platform = platform or row.platform
                row.mode = mode or row.mode
                row.last_seen = _dt.datetime.utcnow()
                s.commit()
                return False
            s.add(PushTokenRow(token=token, platform=platform or "web",
                               mode=mode or "both"))
            s.commit()
            return True

    def push_unregister(self, token: str) -> bool:
        """Remove a device token (app uninstalled / permission revoked)."""
        with self.Session() as s:
            n = s.query(PushTokenRow).filter(
                PushTokenRow.token == token).delete()
            s.commit()
            return bool(n)

    def push_tokens(self) -> List[dict]:
        """All registered tokens (for the event fan-out sender)."""
        with self.Session() as s:
            return [{"token": r.token, "platform": r.platform, "mode": r.mode}
                    for r in s.query(PushTokenRow).all()]

    # ------------------------------------------------------------------
    # Phase 9: Unknown-face cluster helpers (for auto-enrollment).
    #
    # Each row is ONE unknown-face sighting: an embedding + the event it came from.
    # `suggested`: 0 = open/unclustered, 1 = an open "save this?" prompt, 2 =
    # resolved (confirmed or dismissed). The unknown_face_clusters table already
    # exists (Phase 1); we only add methods here.
    # ------------------------------------------------------------------
    def cluster_add(self, embedding: bytes, event_id: str,
                    now_iso: str = "") -> None:
        with self.Session() as s:
            s.add(UnknownClusterRow(cluster_id="", embedding=embedding,
                                    event_id=event_id,
                                    created_at=_parse_iso(now_iso), suggested=0))
            s.commit()

    def cluster_rows(self, include_resolved: bool = False) -> List[dict]:
        with self.Session() as s:
            q = s.query(UnknownClusterRow)
            if not include_resolved:
                q = q.filter(UnknownClusterRow.suggested != 2)
            rows = q.order_by(UnknownClusterRow.id.asc()).all()
            return [{"id": r.id, "cluster_id": r.cluster_id,
                     "embedding": r.embedding, "event_id": r.event_id,
                     "suggested": r.suggested} for r in rows]

    def cluster_update(self, row_ids, cluster_id=None, suggested=None) -> None:
        if not row_ids:
            return
        with self.Session() as s:
            rows = (s.query(UnknownClusterRow)
                    .filter(UnknownClusterRow.id.in_(list(row_ids))).all())
            for r in rows:
                if cluster_id is not None:
                    r.cluster_id = cluster_id
                if suggested is not None:
                    r.suggested = suggested
            s.commit()

    def cluster_count(self) -> int:
        with self.Session() as s:
            return s.query(UnknownClusterRow).count()

    # ------------------------------------------------------------------
    # Phase 13: Known-face persistence helpers.
    #
    # The known_faces table durably stores one row per ENROLLED photo (name +
    # source path + raw float32 embedding bytes). Recognition itself still
    # (re)builds its in-memory gallery from the saved photos on disk via
    # FaceModule.load_known() on boot; these rows are the durable record so an
    # upload survives a restart and a person can be deleted cleanly. Embeddings
    # are stored as np.tobytes(), matching the reid_gallery convention. All three
    # helpers are best-effort: a DB hiccup must never block the in-memory enroll.
    # The known_faces table already exists (Phase 1); we only add methods here.
    # ------------------------------------------------------------------
    def known_face_add(self, name: str, source_path: str = "",
                       embedding: bytes = b"") -> None:
        with self.Session() as s:
            s.add(KnownFaceRow(name=name, source_path=source_path,
                               embedding=embedding))
            s.commit()

    def known_face_delete(self, name: str) -> int:
        """Delete every stored embedding row for `name`; return the row count."""
        with self.Session() as s:
            rows = (s.query(KnownFaceRow)
                    .filter(KnownFaceRow.name == name).all())
            n = len(rows)
            for r in rows:
                s.delete(r)
            s.commit()
            return n

    def known_face_counts(self) -> List[dict]:
        """[{name, count}] of durably-stored embeddings per person."""
        with self.Session() as s:
            rows = s.query(KnownFaceRow).all()
        counts: dict = {}
        for r in rows:
            counts[r.name] = counts.get(r.name, 0) + 1
        return [{"name": n, "count": c} for n, c in sorted(counts.items())]

    # ------------------------------------------------------------------
    @staticmethod
    def _event_row_to_dict(r: EventRow) -> dict:
        """Rebuild a VisitorEvent-shaped dict (nested identity, parsed lists).
        Adds the derived alert_kind, matching VisitorEvent.to_dict()."""
        d = {
            "event_id": r.event_id,
            "timestamp": r.timestamp,
            "trigger": r.trigger,
            "identity": {
                "known": bool(r.identity_known),
                "name": r.identity_name,
                "confidence": r.identity_conf,
            },
            "is_spoof": bool(r.is_spoof),
            "spoof_score": r.spoof_score,
            "visitor_count": r.visitor_count,
            "carried_objects": _safe_json_list(r.carried_objects),
            "scene_summary": r.scene_summary,
            "ocr_text": r.ocr_text,
            "people": _safe_json_list(getattr(r, "people", None) or "[]"),
            "extra_unknown": int(getattr(r, "extra_unknown", 0) or 0),
            "age": r.age,
            "gender": r.gender or "",
            "appearance": r.appearance or "",
            "speech_transcript": r.speech_transcript,
            "language_detected": r.language_detected,
            "translated_transcript": r.translated_transcript,
            "reid_id": r.reid_id,
            "reid_seen_count": r.reid_seen_count,
            "intent": r.intent,
            "confidence": r.confidence,
            "announcement_text": r.announcement_text,
            "snapshot_path": r.snapshot_path,
        }
        d["alert_kind"] = alert_kind(d)
        return d


def _safe_json_list(s: str) -> list:
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _parse_iso(s: str) -> _dt.datetime:
    """Parse an ISO timestamp (the event's own clock) to a naive datetime.

    Falls back to utcnow() on empty/garbage input so recency math never crashes.
    Using the event's clock consistently (store + compare) avoids local-vs-UTC
    skew in the Re-ID TTL window.
    """
    try:
        return _dt.datetime.fromisoformat(s) if s else _dt.datetime.utcnow()
    except Exception:
        return _dt.datetime.utcnow()
