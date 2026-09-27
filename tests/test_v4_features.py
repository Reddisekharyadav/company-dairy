"""
Integration test for v4.0 Microsoft Recall-inspired features.
Tests: dedup, privacy rules, embeddings, search engine, FTS5, database models.
"""
import sys
import os

# Add project root to path
sys.path.insert(0, r'd:\project\companydairy')
os.environ.setdefault('APPDATA', os.path.expanduser('~'))

def test_dedup():
    """Test perceptual hash deduplication."""
    print("=" * 60)
    print("TEST: Perceptual Hash Deduplication")
    print("=" * 60)
    
    from PIL import Image
    from ocr.dedup import compute_dhash, hamming_distance, FrameDeduplicator
    
    # Create test images
    img1 = Image.new('RGB', (100, 100), color=(255, 0, 0))  # Red
    img2 = Image.new('RGB', (100, 100), color=(255, 0, 0))  # Same red (duplicate)
    img3 = Image.new('RGB', (100, 100), color=(0, 0, 255))  # Blue (different)
    
    hash1 = compute_dhash(img1)
    hash2 = compute_dhash(img2)
    hash3 = compute_dhash(img3)
    
    dist_same = hamming_distance(hash1, hash2)
    dist_diff = hamming_distance(hash1, hash3)
    
    print(f"  Hash of red image:    {hash1:016x}")
    print(f"  Hash of same red:     {hash2:016x}")
    print(f"  Hash of blue image:   {hash3:016x}")
    print(f"  Distance (same):      {dist_same} (should be 0)")
    print(f"  Distance (different): {dist_diff} (should be > 0)")
    
    # Test FrameDeduplicator
    dedup = FrameDeduplicator(max_history=5)
    assert not dedup.is_duplicate(img1), "First frame should not be duplicate"
    dedup.add_frame(img1)
    assert dedup.is_duplicate(img2, threshold=5), "Same image should be duplicate"
    assert not dedup.is_duplicate(img3, threshold=2), "Very different image should not be duplicate"
    
    dedup.reset()
    assert not dedup.is_duplicate(img1), "After reset, nothing should be duplicate"
    
    print("  ✅ All dedup tests passed!")
    return True


def test_privacy_rules():
    """Test privacy exclusion rules."""
    print("\n" + "=" * 60)
    print("TEST: Privacy Exclusion Rules")
    print("=" * 60)
    
    from config.privacy_rules import PrivacyRules, scrub_sensitive_text
    
    rules = PrivacyRules()
    
    # Should block sensitive apps
    assert not rules.should_capture('KeePass.exe', 'Master Password'), \
        "KeePass should be blocked"
    assert not rules.should_capture('chrome.exe', 'My Bank - Login'), \
        "Banking sites should be blocked"
    assert not rules.should_capture('edge.exe', 'InPrivate Browsing'), \
        "InPrivate should be blocked"
    
    # Should allow normal apps
    assert rules.should_capture('code.exe', 'main.py - VS Code'), \
        "VS Code should be allowed"
    assert rules.should_capture('chrome.exe', 'GitHub - Dashboard'), \
        "GitHub should be allowed"
    
    print("  ✅ Privacy rules work correctly!")
    
    # Test text scrubbing
    text_with_cc = "My card is 4111-1111-1111-1111 and SSN is 123-45-6789"
    scrubbed = scrub_sensitive_text(text_with_cc)
    assert '[REDACTED]' in scrubbed, "Credit card should be redacted"
    assert '4111' not in scrubbed, "Card number should be gone"
    assert '123-45-6789' not in scrubbed, "SSN should be gone"
    
    text_with_email = "Contact me at user@example.com please"
    scrubbed2 = scrub_sensitive_text(text_with_email)
    assert 'user@example.com' not in scrubbed2, "Email should be redacted"
    
    print("  ✅ Text scrubbing works correctly!")
    return True


def test_embeddings():
    """Test embedding engine."""
    print("\n" + "=" * 60)
    print("TEST: Embedding Engine")
    print("=" * 60)
    
    from ocr.embeddings import embedding_engine
    
    vec = embedding_engine.encode("Hello world, this is a test of semantic search")
    assert vec is not None, "Embedding should not be None"
    assert len(vec) == 384, f"Embedding should be 384-dim, got {len(vec)}"
    
    vec2 = embedding_engine.encode("Testing another sentence for comparison")
    assert vec2 is not None, "Second embedding should not be None"
    
    similarity = embedding_engine.cosine_similarity(vec, vec2)
    self_sim = embedding_engine.cosine_similarity(vec, vec)
    
    print(f"  Embedding dimensions: {len(vec)}")
    print(f"  Self-similarity:      {self_sim:.4f} (should be ~1.0)")
    print(f"  Cross-similarity:     {similarity:.4f} (should be 0-1)")
    print(f"  Using fallback:       {embedding_engine._using_fallback}")
    
    assert 0.99 <= self_sim <= 1.01, "Self-similarity should be ~1.0"
    assert 0.0 <= similarity <= 1.0, "Cross-similarity should be 0-1"
    
    print("  ✅ Embedding engine works correctly!")
    return True


def test_database_models():
    """Test new ScreenFrame model."""
    print("\n" + "=" * 60)
    print("TEST: Database Models (ScreenFrame)")
    print("=" * 60)
    
    from database.models import ScreenFrame, Base
    
    # Verify ScreenFrame has all required columns
    columns = [c.name for c in ScreenFrame.__table__.columns]
    required = ['id', 'timestamp', 'session_id', 'screenshot_path', 'phash',
                'ocr_text', 'embedding_json', 'privacy_scrubbed', 'detected_category']
    
    for col in required:
        assert col in columns, f"Missing column: {col}"
        print(f"  ✓ Column '{col}' exists")
    
    print(f"  Total columns: {len(columns)}")
    print("  ✅ ScreenFrame model is valid!")
    return True


def test_fts5():
    """Test FTS5 full-text search availability."""
    print("\n" + "=" * 60)
    print("TEST: FTS5 Full-Text Search")
    print("=" * 60)
    
    import sqlite3
    import tempfile
    
    db_path = os.path.join(tempfile.gettempdir(), 'test_fts5.db')
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS test_fts USING fts5(text, source)")
        conn.execute("INSERT INTO test_fts VALUES ('hello world python programming', 'test')")
        conn.execute("INSERT INTO test_fts VALUES ('machine learning artificial intelligence', 'test')")
        conn.commit()
        
        cursor = conn.execute("SELECT * FROM test_fts WHERE test_fts MATCH 'python'")
        results = cursor.fetchall()
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        
        cursor = conn.execute("SELECT * FROM test_fts WHERE test_fts MATCH 'learning'")
        results = cursor.fetchall()
        assert len(results) == 1
        
        conn.close()
        print("  ✅ FTS5 is working correctly!")
        return True
    except Exception as e:
        print(f"  ❌ FTS5 error: {e}")
        return False
    finally:
        try:
            os.remove(db_path)
        except:
            pass


def test_search_engine():
    """Test hybrid search engine."""
    print("\n" + "=" * 60)
    print("TEST: Hybrid Search Engine")
    print("=" * 60)
    
    from ocr.search_engine import HybridSearchEngine
    import tempfile
    import sqlite3
    
    db_path = os.path.join(tempfile.gettempdir(), 'test_search.db')
    try:
        # Create the screen_frames table
        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE screen_frames (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                ocr_text TEXT,
                window_title TEXT,
                analysis_summary TEXT,
                process_name TEXT,
                screenshot_path TEXT,
                detected_category TEXT,
                embedding_json TEXT
            )
        ''')
        conn.execute('''
            INSERT INTO screen_frames VALUES 
            (1, '2024-01-01T10:00:00', 'Python code for machine learning model', 
             'main.py - VS Code', 'Coding in Python', 'code.exe', '/screenshots/1.webp',
             'coding', NULL)
        ''')
        conn.execute('''
            INSERT INTO screen_frames VALUES 
            (2, '2024-01-01T10:30:00', 'Browsing GitHub repository for open source project', 
             'GitHub - Dashboard', 'Browsing GitHub', 'chrome.exe', '/screenshots/2.webp',
             'browsing', NULL)
        ''')
        conn.commit()
        conn.close()
        
        engine = HybridSearchEngine(db_path=db_path)
        engine.setup_fts5()
        engine.index_frame(1, 'Python code for machine learning model', 'main.py - VS Code', 'Coding in Python')
        engine.index_frame(2, 'Browsing GitHub repository for open source project', 'GitHub - Dashboard', 'Browsing GitHub')
        
        # Test keyword search
        results = engine.search_keyword('Python', limit=10)
        print(f"  Keyword 'Python': {len(results)} results")
        assert len(results) >= 1, "Should find at least 1 result for 'Python'"
        
        results = engine.search_keyword('GitHub', limit=10)
        print(f"  Keyword 'GitHub': {len(results)} results")
        assert len(results) >= 1, "Should find at least 1 result for 'GitHub'"
        
        # Test hybrid search
        results = engine.search_hybrid('machine learning', limit=10)
        print(f"  Hybrid 'machine learning': {len(results)} results")
        
        print("  ✅ Search engine works correctly!")
        return True
    except Exception as e:
        print(f"  ❌ Search engine error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            os.remove(db_path)
        except:
            pass


def main():
    print("\n🔬 WorkSense AI v4.0 — Feature Integration Tests\n")
    
    results = {}
    tests = [
        ('Dedup', test_dedup),
        ('Privacy', test_privacy_rules),
        ('Embeddings', test_embeddings),
        ('Models', test_database_models),
        ('FTS5', test_fts5),
        ('Search', test_search_engine),
    ]
    
    for name, test_fn in tests:
        try:
            results[name] = test_fn()
        except Exception as e:
            print(f"\n  ❌ {name} FAILED: {e}")
            import traceback
            traceback.print_exc()
            results[name] = False
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for name, passed_flag in results.items():
        status = "✅ PASS" if passed_flag else "❌ FAIL"
        print(f"  {status}: {name}")
    
    print(f"\n  Total: {passed}/{total} passed")
    
    if passed == total:
        print("\n🎉 All tests passed! Ready for live server testing.\n")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed.\n")
    
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
