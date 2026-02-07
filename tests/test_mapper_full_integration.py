#!/usr/bin/env python3
"""Full integration test for mapper with database."""

import os
import tempfile
import pytest

from nrod_railhub.database import RailDB


def test_full_mapper_integration():
    """Test complete mapper integration: config -> events -> observations -> scores."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        # 1. Initialize database with mapper enabled
        db = RailDB(db_path, enable_mapper=True)
        
        # 2. Verify mapper tables are created
        with db._lock:
            cursor = db._conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'berth_signal%'")
            tables = [row[0] for row in cursor.fetchall()]
            assert 'berth_signal_observations' in tables
            assert 'berth_signal_scores' in tables
        
        # 3. Verify default config is set
        config = db.get_mapper_config()
        assert config['pre_ms'] == 1000
        assert config['post_ms'] == 5000
        assert config['tau_ms'] == 2500
        
        # 4. Update config to custom values
        db.update_mapper_config(pre_ms=2000, post_ms=8000, tau_ms=3000)
        
        # 5. Insert correlated events (step followed by signal)
        # Step at t=1000, signal at t=1100 (100ms after, well within window)
        db.insert_td_berth_event(
            ts_ms=1000,
            ts_iso='2024-01-01T00:00:01.000Z',
            area='EK',
            headcode='2C90',
            msg_type='CA',
            from_berth='0001',
            to_berth='0002',
            descr='2C90'
        )
        
        db.insert_td_signal_event(
            ts_ms=1100,
            ts_iso='2024-01-01T00:00:01.100Z',
            area='EK',
            msg_type='SF',
            address='EK123',
            data='01'
        )
        
        # 6. Manually trigger batch processing
        with db._batch_lock:
            batch_size = len(db._event_batch)
            assert batch_size == 2, f"Expected 2 events in batch, got {batch_size}"
            db._process_mapper_batch()
        
        # 7. Verify observations were created
        with db._lock:
            cursor = db._conn.cursor()
            cursor.execute("SELECT * FROM berth_signal_observations WHERE td_area='EK'")
            observations = cursor.fetchall()
            assert len(observations) > 0, "No observations created"
            
            # Check observation structure
            obs = observations[0]
            assert obs[1] == 'EK'  # td_area
            assert obs[3] == 1000  # step_timestamp
            assert obs[4] == '0001'  # from_berth
            assert obs[5] == '0002'  # to_berth
            assert obs[6] == '2C90'  # descr
            assert obs[8] == 1100  # signal_timestamp
            assert obs[9] == 'EK123'  # address
            assert obs[11] == 100  # dt_ms (1100 - 1000)
            assert obs[12] > 0  # weight (should be positive)
            
            # 8. Verify scores were created/updated
            cursor.execute("SELECT * FROM berth_signal_scores WHERE td_area='EK'")
            scores = cursor.fetchall()
            assert len(scores) > 0, "No scores created"
            
            # Check score structure
            score = scores[0]
            assert score[0] == 'EK'  # td_area
            assert score[1] == '0001'  # from_berth
            assert score[2] == '0002'  # to_berth
            assert score[3] == 'EK123'  # address
            assert score[4] > 0  # score (accumulated weight)
            assert score[5] == 1  # obs_count (first observation)
            assert score[6] == 1100  # last_seen_ts
            assert score[7] == '2024-01-01T00:00:01.100Z'  # last_seen_utc
        
        # 9. Insert another correlated pair and verify score accumulation
        db.insert_td_berth_event(
            ts_ms=2000,
            ts_iso='2024-01-01T00:00:02.000Z',
            area='EK',
            headcode='2C90',
            msg_type='CA',
            from_berth='0001',
            to_berth='0002',
            descr='2C90'
        )
        
        db.insert_td_signal_event(
            ts_ms=2200,
            ts_iso='2024-01-01T00:00:02.200Z',
            area='EK',
            msg_type='SF',
            address='EK123',
            data='01'
        )
        
        # Process second batch
        with db._batch_lock:
            db._process_mapper_batch()
        
        # 10. Verify score was updated (not duplicated)
        with db._lock:
            cursor = db._conn.cursor()
            cursor.execute(
                "SELECT score, obs_count FROM berth_signal_scores WHERE td_area='EK' AND address='EK123'"
            )
            score_data = cursor.fetchone()
            assert score_data is not None
            assert score_data[1] == 2, f"Expected obs_count=2, got {score_data[1]}"
            assert score_data[0] > 0, "Score should be positive"
        
        db.close()
        
    finally:
        os.unlink(db_path)


def test_mapper_uses_custom_config():
    """Test that mapper respects custom configuration from database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        db = RailDB(db_path, enable_mapper=True)
        
        # Set very narrow window (pre=100ms, post=200ms)
        db.update_mapper_config(pre_ms=100, post_ms=200, tau_ms=1000)
        
        # Insert step at t=1000
        db.insert_td_berth_event(
            ts_ms=1000,
            ts_iso='2024-01-01T00:00:01.000Z',
            area='EK',
            headcode='2C90',
            msg_type='CA',
            from_berth='0001',
            to_berth='0002',
            descr='2C90'
        )
        
        # Signal well within window (t=1100, 100ms after step)
        db.insert_td_signal_event(
            ts_ms=1100,
            ts_iso='2024-01-01T00:00:01.100Z',
            area='EK',
            msg_type='SF',
            address='NEAR',
            data='01'
        )
        
        # Signal outside window (t=1500, 500ms after step, > post_ms=200)
        db.insert_td_signal_event(
            ts_ms=1500,
            ts_iso='2024-01-01T00:00:01.500Z',
            area='EK',
            msg_type='SF',
            address='FAR',
            data='01'
        )
        
        # Process batch
        with db._batch_lock:
            db._process_mapper_batch()
        
        # Verify only the near signal was correlated
        with db._lock:
            cursor = db._conn.cursor()
            cursor.execute("SELECT address FROM berth_signal_scores WHERE td_area='EK'")
            addresses = [row[0] for row in cursor.fetchall()]
            
            assert 'NEAR' in addresses, "Signal within window should be correlated"
            assert 'FAR' not in addresses, "Signal outside window should NOT be correlated"
        
        db.close()
        
    finally:
        os.unlink(db_path)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
