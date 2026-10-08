"""
Semantic and Keyword Search Engine (Hybrid Search via FTS5 + sqlite-vec)
Inspired by Microsoft Recall / OpenRecall.
"""
import logging
import json
import struct
import re
from typing import List, Dict, Any
from sqlalchemy import text
from database.session import SessionLocal

log = logging.getLogger('search_engine')

class SearchEngine:
    def __init__(self):
        self._model = None
        self._is_loaded = False
        
    def _lazy_load(self):
        if self._is_loaded:
            return
        self._is_loaded = True
        try:
            log.info("Loading sentence-transformers embedding model...")
            from sentence_transformers import SentenceTransformer
            # all-MiniLM-L6-v2 outputs 384-dimensional vectors
            self._model = SentenceTransformer('all-MiniLM-L6-v2')
            log.info("Embedding model loaded.")
        except Exception as e:
            log.error("Failed to load sentence-transformers: %s", e)
            
    def encode(self, text_str: str) -> List[float]:
        self._lazy_load()
        if not self._model or not text_str:
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

search_engine = SearchEngine()
