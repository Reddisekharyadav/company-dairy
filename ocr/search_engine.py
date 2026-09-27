"""
Semantic and Keyword Search Engine (Hybrid Search via FTS5 + sqlite-vec)
Inspired by Microsoft Recall / OpenRecall.
"""
import logging
import json
import struct
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
            
        try:
            log.info("Loading sentence-transformers embedding model...")
            from sentence_transformers import SentenceTransformer
            # all-MiniLM-L6-v2 outputs 384-dimensional vectors
            self._model = SentenceTransformer('all-MiniLM-L6-v2')
            self._is_loaded = True
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
        Perform a Reciprocal Rank Fusion (RRF) search across FTS5 and sqlite-vec.
        """
        self._lazy_load()
        session = SessionLocal()
        try:
            # 1. FTS5 Keyword Search
            fts_results = {}
            if query:
                # Basic FTS match
                clean_query = ''.join(c if c.isalnum() else ' ' for c in query).strip()
                if clean_query:
                    # SQLite FTS5 syntax
                    fts_sql = text("""
                        SELECT rowid, bm25(screen_frames_fts) as score
                        FROM screen_frames_fts
                        WHERE screen_frames_fts MATCH :query
                        ORDER BY score LIMIT 100
                    """)
                    res = session.execute(fts_sql, {"query": clean_query}).fetchall()
                    for rank, row in enumerate(res):
                        fts_results[row.rowid] = {"rank": rank + 1, "score": row.score}
                        
            # 2. Vector Semantic Search
            vec_results = {}
            query_vector = self.encode(query)
            if query_vector:
                vec_blob = self.serialize_vector(query_vector)
                # sqlite-vec uses knn search
                vec_sql = text("""
                    SELECT rowid, distance
                    FROM screen_frames_vec
                    WHERE embedding MATCH :query_vec
                    ORDER BY distance LIMIT 100
                """)
                res = session.execute(vec_sql, {"query_vec": vec_blob}).fetchall()
                for rank, row in enumerate(res):
                    vec_results[row.rowid] = {"rank": rank + 1, "distance": row.distance}

            # 3. Reciprocal Rank Fusion (RRF)
            # RRF Score = 1 / (k + rank_fts) + 1 / (k + rank_vec)
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
                
            # Sort by RRF score descending
            top_ids = sorted(fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True)[:limit]
            
            if not top_ids:
                return []
                
            # 4. Fetch actual frame data for the top IDs
            # Maintain the RRF sort order
            placeholders = ','.join([str(id) for id in top_ids])
            fetch_sql = text(f"""
                SELECT id, timestamp, process_name, window_title, screenshot_path, ocr_text, analysis_summary, omniparser_json
                FROM screen_frames
                WHERE id IN ({placeholders})
            """)
            frames = session.execute(fetch_sql).fetchall()
            
            # Reorder according to top_ids
            frame_map = {f.id: dict(f._mapping) for f in frames}
            ordered_results = []
            for rowid in top_ids:
                if rowid in frame_map:
                    item = frame_map[rowid]
                    # Convert timestamp to ISO
                    item["timestamp"] = item["timestamp"].isoformat() if item["timestamp"] else None
                    # Parse omniparser_json if present
                    if item["omniparser_json"]:
                        try:
                            item["omniparser_json"] = json.loads(item["omniparser_json"])
                        except:
                            item["omniparser_json"] = None
                    ordered_results.append(item)
                    
            return ordered_results
            
        except Exception as e:
            log.error("Hybrid search failed: %s", e)
            return []
        finally:
            session.close()

search_engine = SearchEngine()
