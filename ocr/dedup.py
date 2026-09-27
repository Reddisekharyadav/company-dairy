"""
Perceptual hash-based screenshot deduplication module.

This module provides a FrameDeduplicator class to detect duplicate or highly
similar images (such as video frames or screenshots) using difference hashing (dHash).
"""
import collections
from typing import Deque
from PIL import Image

def compute_dhash(image: Image.Image, hash_size: int = 8) -> int:
    """
    Compute a difference hash (dHash) for a PIL Image.
    
    Args:
        image: The PIL Image to hash.
        hash_size: The size of the hash. An 8x8 hash size produces a 64-bit integer.
        
    Returns:
        An integer representing the dHash.
    """
    # Resize image to (hash_size + 1, hash_size)
    # Using LANCZOS for high-quality downsampling (or Resampling.LANCZOS in newer PIL)
    try:
        resample_filter = Image.Resampling.LANCZOS
    except AttributeError:
        # Fallback for older Pillow versions
        resample_filter = Image.LANCZOS
        
    resized = image.resize((hash_size + 1, hash_size), resample_filter)
    
    # Convert to grayscale
    gray = resized.convert("L")
    
    # Compare adjacent pixels
    pixels = list(gray.getdata())
    hash_value = 0
    
    for row in range(hash_size):
        for col in range(hash_size):
            # Index of current pixel
            idx = row * (hash_size + 1) + col
            # Compare with the next pixel to the right
            if pixels[idx] > pixels[idx + 1]:
                # Set the corresponding bit to 1
                hash_value |= (1 << (row * hash_size + col))
                
    return hash_value

def hamming_distance(hash1: int, hash2: int) -> int:
    """
    Compute the Hamming distance between two integer hashes.
    
    Args:
        hash1: First integer hash.
        hash2: Second integer hash.
        
    Returns:
        The number of differing bits between the two hashes.
    """
    # XOR the two hashes and count the number of set bits (1s)
    x = hash1 ^ hash2
    return bin(x).count('1')

class FrameDeduplicator:
    """
    Tracks recent frames and identifies duplicates based on dHash Hamming distance.
    """
    
    def __init__(self, max_history: int = 5, hash_size: int = 8):
        """
        Initialize the FrameDeduplicator.
        
        Args:
            max_history: Number of recent frame hashes to keep in history.
            hash_size: The size of the dHash to compute.
        """
        self.max_history = max_history
        self.hash_size = hash_size
        self.history: Deque[int] = collections.deque(maxlen=max_history)
        
    def is_duplicate(self, image: Image.Image, threshold: int = 5) -> bool:
        """
        Check if the given image is a duplicate of any recently added frames.
        
        Args:
            image: The PIL Image to check.
            threshold: The maximum Hamming distance to be considered a duplicate.
            
        Returns:
            True if the image is within `threshold` distance of any frame in history.
        """
        if not self.history:
            return False
            
        current_hash = compute_dhash(image, self.hash_size)
        
        for past_hash in self.history:
            distance = hamming_distance(current_hash, past_hash)
            if distance <= threshold:
                return True
                
        return False
        
    def add_frame(self, image: Image.Image) -> None:
        """
        Compute the hash of the frame and add it to the history.
        
        Args:
            image: The PIL Image to add to history.
        """
        frame_hash = compute_dhash(image, self.hash_size)
        self.history.append(frame_hash)
        
    def reset(self) -> None:
        """
        Clear the frame history.
        """
        self.history.clear()
