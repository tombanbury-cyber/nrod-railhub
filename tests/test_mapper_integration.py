#!/usr/bin/env python3
"""Test mapper integration with database."""

import os
import tempfile
import time
import pytest

from nrod_railhub.database import RailDB


def test_mapper_batch_processing():
    """Test that mapper processes events and creates observations/scores."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        db = RailDB(db_path, enable_mapper=True)
        
        # Insert some berth and signal events
        db.insert_td_berth_event(1000, '2024-01-01T00:00:01.000Z', 'EK', '2C90', 'CA', '0001', '0002', '2C90')
        db.insert_td_signal_event(1500, '2024-01-01T00:00:01.500Z', 'EK', 'SF', 'A123', '01')
        
        # Manually trigger batch processing
        with db._batch_lock:
            db._process_mapper_batch()
        
        # Check that observations were created
        with db._lock:
            cursor = db._conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM berth_signal_observations")
            obs_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM berth_signal_scores")
            score_count = cursor.fetchone()[0]
        
        # We should have at least one observation and one score
        assert obs_count >= 1, f"Expected at least 1 observation, got {obs_count}"
        assert score_count >= 1, f"Expected at least 1 score, got {score_count}"
        
        db.close()
    finally:
        os.unlink(db_path)


def test_mapper_config_loaded():
    """Test that mapper uses config from database."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        db = RailDB(db_path, enable_mapper=True)
        
        # Update config
        db.update_mapper_config(pre_ms=2000, post_ms=10000, tau_ms=5000)
        
        # Get config
        config = db.get_mapper_config()
        
        assert config['pre_ms'] == 2000
        assert config['post_ms'] == 10000
        assert config['tau_ms'] == 5000
        
        db.close()
    finally:
        os.unlink(db_path)


def test_insert_observation_and_score():
    """Test direct insertion of observations and scores."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        db = RailDB(db_path, enable_mapper=True)
        
        # Insert observation
        obs_row = ('EK', None, 1000, '0001', '0002', '2C90', None, 1500, 'A123', '01', 500, 0.8187)
        db.insert_observation(obs_row)
        
        # Insert score
        score_row = ('EK', '0001', '0002', 'A123', 0.8187, 1500, '2024-01-01T00:00:01.500Z', '01')
        db.insert_score(score_row)
        
        # Verify they were inserted
        with db._lock:
            cursor = db._conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM berth_signal_observations")
            obs_count = cursor.fetchone()[0]
            assert obs_count == 1
            
            cursor.execute("SELECT COUNT(*) FROM berth_signal_scores")
            score_count = cursor.fetchone()[0]
            assert score_count == 1
        
        db.close()
    finally:
        os.unlink(db_path)


def test_mapper_disabled():
    """Test that mapper doesn't run when disabled."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        db = RailDB(db_path, enable_mapper=False)
        
        # Insert events
        db.insert_td_berth_event(1000, '2024-01-01T00:00:01.000Z', 'EK', '2C90', 'CA', '0001', '0002', '2C90')
        db.insert_td_signal_event(1500, '2024-01-01T00:00:01.500Z', 'EK', 'SF', 'A123', '01')
        
        time.sleep(1)
        
        # Mapper tables should not exist
        with db._lock:
            cursor = db._conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='berth_signal_observations'")
            tables = cursor.fetchall()
            assert len(tables) == 0, "Mapper tables should not exist when mapper is disabled"
        
        db.close()
    finally:
        os.unlink(db_path)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
