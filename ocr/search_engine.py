"""
Semantic and Keyword Search Engine (Hybrid Search via FTS5 + sqlite-vec)
Inspired by Microsoft Recall / OpenRecall.
"""
import logging
import json
import struct
import re
import sqlite3
import contextlib
from threading import Thread
from typing import List, Dict, Any
from sqlalchemy import text
from database.session import SessionLocal

log = logging.getLogger('search_engine')

class SearchEngine:
    def __init__(self):
        self._model = None
        self._fallback = None
        self._is_loaded = False
        self._loading = False
        self._load_thread = None

    def _lazy_load(self):
        """Start loading the model in the background.

        Screenshot capture must never wait for a model download.  Until the
        model is ready, ``encode`` returns an empty vector and search falls back
        to FTS5 keyword matching.
        """
        if self._is_loaded or self._loading:
            return
        self._loading = True

        def _load():
            try:
                log.info("Loading sentence-transformers embedding model in background...")
                from sentence_transformers import SentenceTransformer
                # all-MiniLM-L6-v2 outputs 384-dimensional vectors
                self._model = SentenceTransformer('all-MiniLM-L6-v2')
                log.info("Embedding model loaded.")
            except ImportError:
                log.warning("sentence_transformers not installed; using SimpleEmbedding fallback.")
                self._fallback = self._make_fallback()
            except Exception as e:
                log.error("Failed to load sentence-transformers: %s", e)
                self._fallback = self._make_fallback()
            finally:
                self._is_loaded = True
                self._loading = False

        self._load_thread = Thread(target=_load, daemon=True, name="embedding-loader")
        self._load_thread.start()

    @staticmethod
    def _make_fallback():
        try:
            from ocr.embeddings import SimpleEmbedding
            return SimpleEmbedding()
        except Exception as fallback_error:
            log.error("SimpleEmbedding fallback failed: %s", fallback_error)
            return None

    def encode(self, text_str: str) -> List[float]:
        self._lazy_load()
        if not text_str:
            return []
        if self._fallback is not None:
            return self._fallback.encode(text_str)
        if not self._model:
            return []
        try:
            vector = self._model.encode(text_str)
            return vector.tolist()
        except Exception as e:
            log.warning("Embedding failed: %s", e)
            return []
            
    def serialize_vector(self, vector: List[float]) -> bytes:
        """Serialize a list of floats into a binary blob for sqlite-vec."""
        return struct.pack(f"{len(vector)}f", *vector)

    def hybrid_search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Perform a Reciprocal Rank Fusion (RRF) search across FTS5 and sqlite-vec,
        with automatic fallback to SQL LIKE search if needed.
        """
        query_str = (query or '').strip()
        if not query_str:
            # If no query, return recent frames
            session = SessionLocal()
            try:
                fetch_sql = text("""
                    SELECT id, timestamp, process_name, window_title, screenshot_path, ocr_text, analysis_summary, detected_category, omniparser_json
                    FROM screen_frames
                    ORDER BY timestamp DESC LIMIT :limit
                """)
                frames = session.execute(fetch_sql, {"limit": limit}).fetchall()
                results = []
                for f in frames:
                    item = dict(f._mapping)
                    ts_val = item.get("timestamp")
                    item["timestamp"] = ts_val.isoformat() if hasattr(ts_val, "isoformat") else (str(ts_val) if ts_val else None)
                    if item.get("omniparser_json"):
                        try:
                            item["omniparser_json"] = json.loads(item["omniparser_json"])
                        except Exception:
                            item["omniparser_json"] = None
                    results.append(item)
                return results
            except Exception as e:
                log.error("Recent frames query failed: %s", e)
                return []
            finally:
                session.close()

        session = SessionLocal()
        try:
            # 1. FTS5 Keyword Search
            fts_results = {}
            try:
                # Sanitize query for FTS5
                clean_words = [w for w in re.findall(r'\w+', query_str) if w]
                if clean_words:
                    fts_query = ' OR '.join(f'"{w}"*' for w in clean_words)
                    fts_sql = text("""
                        SELECT rowid, bm25(screen_frames_fts) as score
                        FROM screen_frames_fts
                        WHERE screen_frames_fts MATCH :query
                        ORDER BY score LIMIT 100
                    """)
                    res = session.execute(fts_sql, {"query": fts_query}).fetchall()
                    for rank, row in enumerate(res):
                        fts_results[row.rowid] = {"rank": rank + 1, "score": row.score}
            except Exception as fts_err:
                log.debug("FTS5 search error (continuing): %s", fts_err)

            # 2. Vector Semantic Search
            vec_results = {}
            try:
                self._lazy_load()
                query_vector = self.encode(query_str)
                if query_vector:
                    vec_blob = self.serialize_vector(query_vector)
                    vec_sql = text("""
                        SELECT rowid, distance
                        FROM screen_frames_vec
                        WHERE embedding MATCH :query_vec
                        ORDER BY distance LIMIT 100
                    """)
                    res = session.execute(vec_sql, {"query_vec": vec_blob}).fetchall()
                    for rank, row in enumerate(res):
                        vec_results[row.rowid] = {"rank": rank + 1, "distance": row.distance}
            except Exception as vec_err:
                log.debug("Vector search error (continuing): %s", vec_err)

            # 3. Reciprocal Rank Fusion (RRF)
            k = 60
            fused_scores = {}
            all_ids = set(fts_results.keys()).union(set(vec_results.keys()))

            for rowid in all_ids:
                score = 0.0
                if rowid in fts_results:
                    score += 1.0 / (k + fts_results[rowid]["rank"])
                if rowid in vec_results:
                    score += 1.0 / (k + vec_results[rowid]["rank"])
                fused_scores[rowid] = score

            top_ids = sorted(fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True)[:limit]

            # 4. Fetch actual frame data for top IDs
            if top_ids:
                placeholders = ','.join([str(id) for id in top_ids])
                fetch_sql = text(f"""
                    SELECT id, timestamp, process_name, window_title, screenshot_path, ocr_text, analysis_summary, detected_category, omniparser_json
                    FROM screen_frames
                    WHERE id IN ({placeholders})
                """)
                frames = session.execute(fetch_sql).fetchall()
                frame_map = {f.id: dict(f._mapping) for f in frames}
                ordered_results = []
                for rowid in top_ids:
                    if rowid in frame_map:
                        item = frame_map[rowid]
                        ts_val = item.get("timestamp")
                        item["timestamp"] = ts_val.isoformat() if hasattr(ts_val, "isoformat") else (str(ts_val) if ts_val else None)
                        if item.get("omniparser_json"):
                            try:
                                item["omniparser_json"] = json.loads(item["omniparser_json"])
                            except Exception:
                                item["omniparser_json"] = None
                        ordered_results.append(item)
                if ordered_results:
                    return ordered_results

            # 5. Fallback: SQL LIKE Search if FTS and Vector produced no matches
            log.debug("FTS/Vector produced 0 results. Executing fallback SQL LIKE search for '%s'", query_str)
            like_term = f"%{query_str}%"
            fallback_sql = text("""
                SELECT id, timestamp, process_name, window_title, screenshot_path, ocr_text, analysis_summary, detected_category, omniparser_json
                FROM screen_frames
                WHERE ocr_text LIKE :like_q OR window_title LIKE :like_q OR process_name LIKE :like_q OR analysis_summary LIKE :like_q
                ORDER BY timestamp DESC LIMIT :limit
            """)
            fallback_frames = session.execute(fallback_sql, {"like_q": like_term, "limit": limit}).fetchall()
            ordered_results = []
            for f in fallback_frames:
                item = dict(f._mapping)
                ts_val = item.get("timestamp")
                item["timestamp"] = ts_val.isoformat() if hasattr(ts_val, "isoformat") else (str(ts_val) if ts_val else None)
                if item.get("omniparser_json"):
                    try:
                        item["omniparser_json"] = json.loads(item["omniparser_json"])
                    except Exception:
                        item["omniparser_json"] = None
                ordered_results.append(item)

            return ordered_results

        except Exception as e:
            log.error("Hybrid search failed: %s", e)
            return []
        finally:
            session.close()


class HybridSearchEngine:
    """Standalone hybrid search engine over a caller-supplied SQLite database.

    This is the original v4.0 API, kept for backward compatibility with scripts
    and tests that manage their own database instead of the application's
    global session. New code should prefer :class:`SearchEngine`.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def setup_fts5(self) -> None:
        """Create the FTS5 index used for keyword search."""
        with self._connect() as conn:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS screen_frames_fts "
                "USING fts5(ocr_text, window_title, analysis_summary)"
            )

    def index_frame(self, frame_id: int, ocr_text: str = "",
                    window_title: str = "", analysis_summary: str = "") -> None:
        """Add or update a frame in the keyword index."""
        with self._connect() as conn:
            conn.execute("DELETE FROM screen_frames_fts WHERE rowid = ?", (frame_id,))
            conn.execute(
                "INSERT INTO screen_frames_fts(rowid, ocr_text, window_title, analysis_summary) "
                "VALUES (?, ?, ?, ?)",
                (frame_id, ocr_text or "", window_title or "", analysis_summary or ""),
            )

    @staticmethod
    def _fts_query(query: str) -> str:
        words = [w for w in re.findall(r"\w+", query or "") if w]
        return " OR ".join(f'"{w}"*' for w in words)

    def search_keyword(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Full-text keyword search using FTS5 (BM25 ranking)."""
        match = self._fts_query(query)
        if not match:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT rowid AS id, ocr_text, window_title, analysis_summary, "
                    "bm25(screen_frames_fts) AS rank "
                    "FROM screen_frames_fts WHERE screen_frames_fts MATCH ? "
                    "ORDER BY rank LIMIT ?",
                    (match, limit),
                ).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            log.debug("FTS5 keyword search failed: %s", exc)
            return []

    def search_hybrid(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Keyword search with a LIKE fallback when FTS returns nothing."""
        results = self.search_keyword(query, limit=limit)
        if results or not (query or "").strip():
            return results
        like_term = f"%{query}%"
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, ocr_text, window_title, analysis_summary "
                    "FROM screen_frames "
                    "WHERE ocr_text LIKE ? OR window_title LIKE ? "
                    "OR analysis_summary LIKE ? LIMIT ?",
                    (like_term, like_term, like_term, limit),
                ).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            log.debug("LIKE fallback search failed: %s", exc)
            return []


search_engine = SearchEngine()
