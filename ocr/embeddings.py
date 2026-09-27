"""
Semantic search embedding module.

This module provides vector embedding generation and storage functionality
for semantic search of OCR text. It uses the sentence-transformers library
with the 'all-MiniLM-L6-v2' model for high-quality embeddings. It also
includes a pure-Python fallback for environments without sentence-transformers.
"""

import logging
import math
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)

class SimpleEmbedding:
    """
    A pure-Python fallback embedding generator using a basic bag-of-words approach
    hashed into 384 dimensions to match the output size of the primary model.
    """
    
    DIMENSIONS = 384

    def encode(self, text: str) -> List[float]:
        """Encodes text into a 384-dimensional vector."""
        vec = [0.0] * self.DIMENSIONS
        words = text.lower().split()
        if not words:
            return vec
        
        for word in words:
            # Simple hash-based bucketing
            idx = hash(word) % self.DIMENSIONS
            vec[idx] += 1.0
            
        # L2 Normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
            
        return vec

    def encode_batch(self, texts: List[str]) -> List[List[float]]:
        """Encodes a batch of texts."""
        return [self.encode(text) for text in texts]


class EmbeddingEngine:
    """
    Primary embedding engine that lazily loads the sentence-transformers model.
    Uses SimpleEmbedding as a fallback if the library is not installed.
    """
    
    def __init__(self, model_name: str = 'all-MiniLM-L6-v2'):
        self.model_name = model_name
        self._model = None
        self._fallback = None
        self._lock = threading.Lock()
        self._initialized = False
        self._using_fallback = False

    def _initialize(self):
        """Lazily initialize the model or fallback."""
        if self._initialized:
            return

        with self._lock:
            # Double-checked locking
            if self._initialized:
                return
                
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name)
                logger.info(f"Successfully loaded sentence-transformers model: {self.model_name}")
            except ImportError:
                logger.warning("sentence_transformers not found. Falling back to SimpleEmbedding.")
                self._fallback = SimpleEmbedding()
                self._using_fallback = True
            except Exception as e:
                logger.warning(f"Error loading model {self.model_name}: {e}. Falling back to SimpleEmbedding.")
                self._fallback = SimpleEmbedding()
                self._using_fallback = True
                
            self._initialized = True

    def encode(self, text: str) -> Optional[List[float]]:
        """
        Encode a single text string into an embedding vector.
        """
        self._initialize()
        
        if self._using_fallback and self._fallback:
            return self._fallback.encode(text)
            
        if self._model:
            try:
                embedding = self._model.encode(text)
                return embedding.tolist()
            except Exception as e:
                logger.error(f"Error encoding text: {e}")
                return None
        return None

    def encode_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """
        Encode a batch of texts.
        """
        self._initialize()
        
        if self._using_fallback and self._fallback:
            return self._fallback.encode_batch(texts)
            
        if self._model:
            try:
                embeddings = self._model.encode(texts)
                return embeddings.tolist()
            except Exception as e:
                logger.error(f"Error encoding batch: {e}")
                return None
        return None

    def cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        """
        Calculate cosine similarity between two vectors.
        """
        if not vec_a or not vec_b:
            return 0.0
            
        if len(vec_a) != len(vec_b):
            raise ValueError("Vectors must have the same length")
            
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
            
        return dot_product / (norm_a * norm_b)


# Module-level singleton
embedding_engine = EmbeddingEngine()
