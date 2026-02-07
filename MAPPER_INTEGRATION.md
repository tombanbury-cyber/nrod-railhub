# Mapper Integration Summary

## Problem Statement
The mapper logic existed but was not integrated with the database. Specifically:
1. No methods to insert observations and scores into database
2. Mapper was never instantiated or called to process events
3. Mapper configuration was not loaded from database

## Solution Implemented

### 1. Database Methods Added (nrod_railhub/database.py)

#### `insert_observation(obs_row: tuple)`
- Inserts berth-signal correlation observations
- Uses ON CONFLICT DO NOTHING to handle duplicates
- Row format: (td_area, step_event_id, step_timestamp, from_berth, to_berth, descr, signal_event_id, signal_timestamp, address, data, dt_ms, weight)

#### `insert_score(score_row: tuple)`
- Inserts or updates accumulated correlation scores
- Automatically increments obs_count on conflicts
- Updates last_seen_ts and last_seen_utc with latest values
- Row format: (td_area, from_berth, to_berth, address, score, last_seen_ts, last_seen_utc, last_data)

### 2. Batch Processing System

#### Event Collection
- `insert_td_berth_event()` and `insert_td_signal_event()` now add events to `_event_batch`
- Batch size threshold: 100 events (configurable via `_batch_size`)

#### Batch Processing
- `_process_mapper_batch()`: Processes accumulated events using `process_batch_for_mapper()`
- Loads configuration from database (pre_ms, post_ms, tau_ms)
- Inserts resulting observations and scores
- Background thread runs every 10 seconds to process any pending events

#### Thread Safety
- `_batch_lock`: Protects event batch from concurrent modification
- `_lock`: Protects database operations
- All batch operations are thread-safe

### 3. Configuration Management

The mapper now uses configuration from the database:
```python
config = db.get_mapper_config()  # Loads from mapper_config table
pre_ms = config.get('pre_ms', 1000)
post_ms = config.get('post_ms', 5000)
tau_ms = config.get('tau_ms', 2500)
```

Default values are inserted when database is created:
- pre_ms: 1000 (1 second before step)
- post_ms: 5000 (5 seconds after step)
- tau_ms: 2500 (exponential weighting time constant)

Configuration can be updated:
```python
db.update_mapper_config(pre_ms=2000, post_ms=8000, tau_ms=3000)
```

### 4. Database Schema

The mapper tables were already defined but are now actually populated:

#### berth_signal_observations
- Stores individual step-signal correlations
- Unique index on (td_area, step_timestamp, signal_timestamp, address)
- Includes dt_ms (time delta) and weight (correlation score)

#### berth_signal_scores
- Accumulates correlation evidence over time
- Primary key: (td_area, from_berth, to_berth, address)
- Tracks obs_count (number of observations) and cumulative score

#### mapper_config
- Stores mapper parameters
- Keys: pre_ms, post_ms, tau_ms
- Includes updated_at_utc timestamp

## Integration Flow

```
TD Event Received
       ↓
insert_td_berth_event() or insert_td_signal_event()
       ↓
_add_event_to_batch()
       ↓
[Batch accumulates until size=100 or 10s timer expires]
       ↓
_process_mapper_batch()
       ↓
Load config from database
       ↓
process_batch_for_mapper(events, pre_ms, post_ms, tau_ms)
       ↓
Returns (obs_rows, score_rows)
       ↓
insert_observation() for each obs_row
insert_score() for each score_row
       ↓
Database updated with correlations
```

## Testing

### Test Coverage
- **test_mapper_integration.py**: 4 tests
  - Batch processing functionality
  - Config loading/updating
  - Direct insert methods
  - Mapper disabled mode

- **test_mapper_full_integration.py**: 2 comprehensive tests
  - Full end-to-end integration
  - Custom config usage and time window filtering

### Test Results
- All 43 tests pass (37 existing + 6 new)
- Coverage includes:
  - Schema creation
  - Config management
  - Event batching
  - Observation insertion
  - Score accumulation
  - Thread safety
  - Edge cases (empty batches, missing correlations, etc.)

## Usage

### Enable Mapper (Default)
```python
db = RailDB("/path/to/db.db", enable_mapper=True)
```

### Disable Mapper
```python
db = RailDB("/path/to/db.db", enable_mapper=False)
```

### Update Configuration
```python
db.update_mapper_config(pre_ms=2000, post_ms=8000, tau_ms=3000)
```

### Query Results
```sql
-- Top correlations for a specific edge
SELECT address, score, obs_count 
FROM berth_signal_scores 
WHERE td_area='EK' AND from_berth='0001' AND to_berth='0002'
ORDER BY score DESC;

-- All observations for a step event
SELECT * FROM berth_signal_observations
WHERE td_area='EK' AND step_timestamp=1234567890;
```

## Performance Considerations

1. **Batch Size**: Default 100 events. Can be adjusted via `_batch_size`
2. **Processing Interval**: Background thread runs every 10 seconds
3. **Database Locking**: WAL mode enabled for better concurrency
4. **Memory Usage**: Batch is cleared after processing
5. **Thread Safety**: All operations use appropriate locks

## Future Enhancements

Possible improvements:
1. Make batch_size and processing interval configurable
2. Add metrics/logging for batch processing performance
3. Implement age-based batch processing (e.g., process if oldest event > 30s)
4. Add API to manually trigger batch processing
5. Expose batch status via web dashboard

## Compatibility

- No breaking changes to existing functionality
- Mapper is optional (can be disabled)
- Existing tests continue to pass
- No changes to public API (except new methods)
